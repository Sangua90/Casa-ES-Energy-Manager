"""Casa ES Energy Manager v1.5.9 climate stability fixes.

Climate/PDC loads use the configured anti-cycle values without legacy runtime
rewrites, normal stop requests require persistence, daily activation limits only
block future starts, and phase-only overload remains advisory. True inverter
hard-safety events still bypass persistence and may stop loads immediately.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_DEVICE_MIN_OFF_MINUTES,
    CONF_DEVICE_MIN_ON_MINUTES,
    DEVICE_MODE_AUTO,
    DEVICE_TYPE_CLIMATE,
)
from .coordinator_v157 import CasaESEnergyCoordinator as V157Coordinator

CONF_DEVICE_STOP_PERSISTENCE_MINUTES = "stop_persistence_minutes"
DEFAULT_CLIMATE_STOP_PERSISTENCE_MINUTES = 20.0


class CasaESEnergyCoordinator(V157Coordinator):
    """v1.5.9 adds stable 20/20/20 climate semantics and phase advisory safety."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._climate_stop_pending_since: dict[str, Any] = {}

    def _managed_device_snapshots(self) -> list[dict[str, Any]]:
        """Restore persisted climate anti-cycle values after legacy v1.5.1 logic."""
        devices = super()._managed_device_snapshots()
        configured = {
            str(subentry.subentry_id): dict(subentry.data)
            for subentry in self.entry.subentries.values()
        }
        for item in devices:
            if str(item.get("device_type") or "") != DEVICE_TYPE_CLIMATE:
                continue
            source = configured.get(str(item.get("subentry_id") or ""), {})
            if CONF_DEVICE_MIN_ON_MINUTES in source:
                item[CONF_DEVICE_MIN_ON_MINUTES] = float(
                    source.get(CONF_DEVICE_MIN_ON_MINUTES) or 0.0
                )
            if CONF_DEVICE_MIN_OFF_MINUTES in source:
                item[CONF_DEVICE_MIN_OFF_MINUTES] = float(
                    source.get(CONF_DEVICE_MIN_OFF_MINUTES) or 0.0
                )
            if CONF_DEVICE_STOP_PERSISTENCE_MINUTES in source:
                item[CONF_DEVICE_STOP_PERSISTENCE_MINUTES] = float(
                    source.get(CONF_DEVICE_STOP_PERSISTENCE_MINUTES) or 0.0
                )
            item.pop("anti_cycle_profile_migrated_v151", None)
            item["anti_cycle_profile_source"] = "persisted_v159"
        return devices

    @staticmethod
    def _activation_limit_only_blocks_future_start(decision: dict[str, Any]) -> bool:
        return bool(
            decision.get("entity_active")
            and str(decision.get("reason") or "").startswith(
                "Numero massimo di avvii giornalieri raggiunto"
            )
        )

    def _normalize_climate_stop_requests(self, data: dict[str, Any]) -> None:
        """Remove two false immediate-stop paths before persistence is evaluated."""
        phase_only = bool(
            data.get("phase_warning")
            and not data.get("inverter_warning")
            and not data.get("grid_warning")
        )
        configs = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("managed_device_configs") or [])
        }

        for decision in data.get("dry_run_decisions") or []:
            source = configs.get(str(decision.get("subentry_id") or ""), {})
            if str(source.get("device_type") or "") != DEVICE_TYPE_CLIMATE:
                continue

            # Maximum daily activations means "do not start again". It must not
            # terminate a climate/PDC that is already running.
            if self._activation_limit_only_blocks_future_start(decision):
                decision["would_stop"] = False
                decision["stop_is_hard_safety"] = False
                decision["decision"] = "activation_limit_blocks_restart_only"
                decision["reason"] = (
                    "Numero massimo di avvii giornalieri raggiunto: il climatizzatore "
                    "già acceso resta attivo; il limite blocca soltanto un nuovo avvio."
                )

            # A per-phase warning is intentionally advisory in Casa ES. Only a
            # true inverter total overload retains hard-safety authority here.
            if (
                phase_only
                and decision.get("stop_is_hard_safety")
                and decision.get("would_stop")
            ):
                decision["would_stop"] = False
                decision["stop_is_hard_safety"] = False
                decision["decision"] = "phase_warning_advisory"
                decision["reason"] = (
                    "Margine fase ridotto: solo diagnostica, nessuno spegnimento "
                    "automatico del climatizzatore senza allarme inverter/rete."
                )
                decision["phase_warning_advisory_only"] = True

    def _apply_climate_stop_persistence(self, data: dict[str, Any], now: Any) -> None:
        self._normalize_climate_stop_requests(data)

        configs = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("managed_device_configs") or [])
        }
        seen: set[str] = set()

        for decision in data.get("dry_run_decisions") or []:
            subentry_id = str(decision.get("subentry_id") or "")
            if not subentry_id:
                continue
            source = configs.get(subentry_id) or {}
            if str(source.get("device_type") or "") != DEVICE_TYPE_CLIMATE:
                self._climate_stop_pending_since.pop(subentry_id, None)
                continue

            seen.add(subentry_id)
            persistence_minutes = max(
                float(
                    source.get(
                        CONF_DEVICE_STOP_PERSISTENCE_MINUTES,
                        DEFAULT_CLIMATE_STOP_PERSISTENCE_MINUTES,
                    )
                    or 0.0
                ),
                0.0,
            )
            decision[CONF_DEVICE_STOP_PERSISTENCE_MINUTES] = persistence_minutes

            # Only true hard electrical protection (e.g. inverter total overload)
            # may bypass the normal persistence timer.
            if decision.get("stop_is_hard_safety"):
                self._climate_stop_pending_since.pop(subentry_id, None)
                decision["stop_persistence_state"] = "bypassed_hard_safety"
                continue

            normal_stop_requested = bool(
                decision.get("would_stop")
                and decision.get("management_mode", DEVICE_MODE_AUTO) == DEVICE_MODE_AUTO
                and decision.get("entity_active")
            )
            if not normal_stop_requested:
                self._climate_stop_pending_since.pop(subentry_id, None)
                decision["stop_persistence_state"] = "clear"
                decision["stop_persistence_elapsed_minutes"] = 0.0
                continue

            if persistence_minutes <= 0:
                self._climate_stop_pending_since.pop(subentry_id, None)
                decision["stop_persistence_state"] = "satisfied"
                continue

            started = self._climate_stop_pending_since.get(subentry_id)
            if started is None:
                started = now
                self._climate_stop_pending_since[subentry_id] = started

            elapsed_seconds = max((now - started).total_seconds(), 0.0)
            required_seconds = persistence_minutes * 60.0
            decision["stop_persistence_elapsed_minutes"] = round(
                elapsed_seconds / 60.0, 1
            )

            if elapsed_seconds + 1e-9 < required_seconds:
                remaining_minutes = max(required_seconds - elapsed_seconds, 0.0) / 60.0
                original_reason = str(
                    decision.get("reason") or "Condizione di arresto energetico."
                )
                decision["would_stop"] = False
                decision["decision"] = "stop_persistence_wait"
                decision["reason"] = (
                    f"{original_reason} Attendo che la condizione resti continua per "
                    f"{persistence_minutes:.0f} min; circa {remaining_minutes:.0f} min residui."
                )
                decision["stop_persistence_state"] = "waiting"
            else:
                decision["stop_persistence_state"] = "satisfied"

        for subentry_id in list(self._climate_stop_pending_since):
            if subentry_id not in seen:
                self._climate_stop_pending_since.pop(subentry_id, None)

        decisions = data.get("dry_run_decisions") or []
        data["managed_devices_would_stop"] = sum(
            1 for item in decisions if item.get("would_stop")
        )
        data["v159_climate_stop_persistence"] = {
            "default_minutes": DEFAULT_CLIMATE_STOP_PERSISTENCE_MINUTES,
            "pending": {
                key: value.isoformat()
                for key, value in self._climate_stop_pending_since.items()
            },
            "phase_only_shed": "disabled",
            "activation_limit_behavior": "blocks_restart_only",
            "configured_anti_cycle_restored": True,
            "hard_safety_bypass": "inverter_or_true_hard_safety_only",
        }

    def _enforce_cross_type_priority(self, data: dict[str, Any], now: Any) -> None:
        """Run battery/priority logic, then apply final climate stop semantics."""
        super()._enforce_cross_type_priority(data, now)
        self._apply_climate_stop_persistence(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v158_climate_stop_persistence_supported"] = True
        data["v159_climate_stability_fix"] = True
        return data

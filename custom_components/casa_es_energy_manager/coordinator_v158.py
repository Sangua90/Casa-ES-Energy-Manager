"""Casa ES Energy Manager v1.5.8 climate stop-persistence support.

Normal energy-optimization stops for climate/PDC loads must remain continuously
requested for a configurable amount of time before Casa ES actually stops the
appliance. Hard electrical safety stops still bypass this delay.
"""

from __future__ import annotations

from typing import Any

from .const import DEVICE_MODE_AUTO, DEVICE_TYPE_CLIMATE
from .coordinator_v157 import CasaESEnergyCoordinator as V157Coordinator

CONF_DEVICE_STOP_PERSISTENCE_MINUTES = "stop_persistence_minutes"
DEFAULT_CLIMATE_STOP_PERSISTENCE_MINUTES = 20.0


class CasaESEnergyCoordinator(V157Coordinator):
    """v1.5.8 adds continuous-deficit persistence before normal climate stops."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._climate_stop_pending_since: dict[str, Any] = {}

    def _apply_climate_stop_persistence(self, data: dict[str, Any], now: Any) -> None:
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

            # Hard electrical protection always keeps immediate authority.
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
            elapsed_minutes = elapsed_seconds / 60.0
            decision["stop_persistence_elapsed_minutes"] = round(elapsed_minutes, 1)

            if elapsed_seconds + 1e-9 < required_seconds:
                remaining_minutes = max(required_seconds - elapsed_seconds, 0.0) / 60.0
                original_reason = str(decision.get("reason") or "Condizione di arresto energetico.")
                decision["would_stop"] = False
                decision["decision"] = "stop_persistence_wait"
                decision["reason"] = (
                    f"{original_reason} Attendo che la condizione resti continua per "
                    f"{persistence_minutes:.0f} min; circa {remaining_minutes:.0f} min residui."
                )
                decision["stop_persistence_state"] = "waiting"
            else:
                decision["stop_persistence_state"] = "satisfied"

        # Remove stale timers for deleted/non-present climate subentries.
        for subentry_id in list(self._climate_stop_pending_since):
            if subentry_id not in seen:
                self._climate_stop_pending_since.pop(subentry_id, None)

        decisions = data.get("dry_run_decisions") or []
        data["managed_devices_would_stop"] = sum(
            1 for item in decisions if item.get("would_stop")
        )
        data["v158_climate_stop_persistence"] = {
            "default_minutes": DEFAULT_CLIMATE_STOP_PERSISTENCE_MINUTES,
            "pending": {
                key: value.isoformat()
                for key, value in self._climate_stop_pending_since.items()
            },
            "hard_safety_bypass": True,
        }

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        self._apply_climate_stop_persistence(data, now)
        await super()._async_apply_real_control(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v158_climate_stop_persistence_supported"] = True
        return data

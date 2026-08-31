"""Casa ES Energy Manager v1.5.12 climate start/stop stability.

v1.5.12 prevents a climate/PDC from starting exactly on its minimum battery SOC
unless current solar opportunity can practically cover the whole start. It also
binds the 20-minute normal-stop persistence timer to one continuous stop
condition: if the condition clears or changes, the timer is cancelled/reset and
the climate remains on. True hard electrical safety still bypasses persistence.
"""

from __future__ import annotations

import re
from typing import Any

from .const import (
    CONF_DEVICE_MIN_BATTERY_SOC,
    CONF_DEVICE_TYPE,
    DEVICE_MODE_AUTO,
    DEVICE_TYPE_CLIMATE,
)
from .coordinator_v1511 import CasaESEnergyCoordinator as V1511Coordinator

CLIMATE_START_SOC_MARGIN_PCT = 2.0
CLIMATE_SOLAR_COVER_FRACTION = 0.90


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V1511Coordinator):
    """v1.5.12 coordinator with climate anti-chatter admission/stop rules."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._v1512_stop_condition_key: dict[str, str] = {}

    @staticmethod
    def _continuous_stop_condition_key(decision: dict[str, Any]) -> str:
        """Build a stable key for the currently requested normal stop condition."""
        code = str(decision.get("decision") or "normal_stop")
        reason = str(decision.get("reason") or "")
        # Persistence text is appended by the previous refresh and numeric values
        # naturally fluctuate; neither should create a new logical condition.
        reason = reason.split(" Attendo che la condizione resti continua", 1)[0]
        reason = re.sub(r"\d+(?:[\.,]\d+)?", "#", reason)
        return f"{code}|{reason.strip()}"

    def _apply_climate_stop_persistence(self, data: dict[str, Any], now: Any) -> None:
        """Require the *same* normal stop condition to persist continuously."""
        self._normalize_climate_stop_requests(data)
        configs = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("managed_device_configs") or [])
        }
        active_ids: set[str] = set()

        for decision in data.get("dry_run_decisions") or []:
            subentry_id = str(decision.get("subentry_id") or "")
            source = configs.get(subentry_id) or {}
            if not subentry_id or str(source.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_CLIMATE:
                continue
            active_ids.add(subentry_id)

            # Hard electrical protection remains immediate and does not share a
            # persistence timer with ordinary energy-management stop requests.
            if decision.get("stop_is_hard_safety"):
                self._v1512_stop_condition_key.pop(subentry_id, None)
                continue

            normal_stop_requested = bool(
                decision.get("would_stop")
                and decision.get("management_mode", DEVICE_MODE_AUTO) == DEVICE_MODE_AUTO
                and decision.get("entity_active")
            )
            if not normal_stop_requested:
                # This is the important cancellation path: if a short high-load
                # event has ended, any pending 20-minute stop is forgotten now.
                self._climate_stop_pending_since.pop(subentry_id, None)
                self._v1512_stop_condition_key.pop(subentry_id, None)
                decision["stop_persistence_cancelled_condition_cleared"] = True
                continue

            key = self._continuous_stop_condition_key(decision)
            previous = self._v1512_stop_condition_key.get(subentry_id)
            if previous is not None and previous != key:
                # A different reason is a new event: it must earn a fresh full
                # persistence interval rather than inheriting elapsed time.
                self._climate_stop_pending_since.pop(subentry_id, None)
                decision["stop_persistence_reset_condition_changed"] = True
            self._v1512_stop_condition_key[subentry_id] = key
            decision["stop_persistence_condition_key"] = key

        for subentry_id in list(self._v1512_stop_condition_key):
            if subentry_id not in active_ids:
                self._v1512_stop_condition_key.pop(subentry_id, None)
                self._climate_stop_pending_since.pop(subentry_id, None)

        # v1.5.9 performs the actual configured persistence timing. Because we
        # cleared/reset its pending timestamp above when necessary, a stop can be
        # satisfied only while the current condition is still present now.
        super()._apply_climate_stop_persistence(data, now)

        diag = data.get("v159_climate_stop_persistence")
        if isinstance(diag, dict):
            diag["continuous_condition_required_v1512"] = True
            diag["cancel_when_condition_clears"] = True
            diag["reset_when_condition_changes"] = True
            diag["condition_keys"] = dict(self._v1512_stop_condition_key)

    def _apply_climate_near_min_soc_start_gate(self, data: dict[str, Any]) -> None:
        """Avoid starting a climate on the SOC floor unless solar covers it."""
        configs = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("managed_device_configs") or [])
        }
        soc = _number(data.get("battery_soc"), 0.0)
        measured_surplus = max(_number(data.get("solar_after_house_w"), 0.0), 0.0)
        potential_surplus = max(_number(data.get("pv_potential_after_house_w"), 0.0), 0.0)
        available_solar = max(measured_surplus, potential_surplus)
        blocked: list[dict[str, Any]] = []

        for decision in data.get("dry_run_decisions") or []:
            if not decision.get("would_start") or decision.get("entity_active"):
                continue
            subentry_id = str(decision.get("subentry_id") or "")
            source = configs.get(subentry_id) or {}
            if str(source.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_CLIMATE:
                continue
            if decision.get("management_mode", DEVICE_MODE_AUTO) != DEVICE_MODE_AUTO:
                continue

            min_soc = _number(source.get(CONF_DEVICE_MIN_BATTERY_SOC), 0.0)
            protected_soc = min_soc + CLIMATE_START_SOC_MARGIN_PCT
            admission_w = max(
                _number(
                    decision.get("admission_power_w"),
                    decision.get("nominal_power_w", source.get("nominal_power_w", 0.0)),
                ),
                0.0,
            )
            solar_needed = admission_w * CLIMATE_SOLAR_COVER_FRACTION
            solar_covers = admission_w > 0.0 and available_solar >= solar_needed

            decision["climate_start_soc_margin_pct"] = CLIMATE_START_SOC_MARGIN_PCT
            decision["climate_start_protected_soc_pct"] = round(protected_soc, 1)
            decision["climate_start_available_solar_w"] = round(available_solar, 1)
            decision["climate_start_solar_cover_required_w"] = round(solar_needed, 1)
            decision["climate_start_solar_cover_sufficient"] = solar_covers

            if soc + 1e-9 >= protected_soc or solar_covers:
                continue

            decision["would_start"] = False
            decision["decision"] = "waiting_climate_soc_margin"
            decision["reason"] = (
                f"Clima vicino al SOC minimo: batteria {soc:.0f}%, minimo {min_soc:.0f}%. "
                f"Per un nuovo avvio attendo almeno {protected_soc:.0f}% oppure FV disponibile "
                f"che copra quasi tutto il carico ({available_solar:.0f}/{solar_needed:.0f} W)."
            )
            blocked.append(
                {
                    "subentry_id": subentry_id,
                    "name": decision.get("name"),
                    "soc_pct": round(soc, 1),
                    "protected_soc_pct": round(protected_soc, 1),
                    "available_solar_w": round(available_solar, 1),
                    "solar_cover_required_w": round(solar_needed, 1),
                }
            )

        data["v1512_climate_start_soc_gate"] = {
            "margin_pct": CLIMATE_START_SOC_MARGIN_PCT,
            "solar_cover_fraction": CLIMATE_SOLAR_COVER_FRACTION,
            "rule": "min_soc_plus_margin_or_near_full_solar_cover",
            "blocked": blocked,
        }

    def _enforce_cross_type_priority(self, data: dict[str, Any], now: Any) -> None:
        """Preserve global priorities, then apply the final climate start gate."""
        super()._enforce_cross_type_priority(data, now)
        self._apply_climate_near_min_soc_start_gate(data)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v1512_climate_continuous_stop_condition"] = True
        data["v1512_climate_stop_cancel_on_recovery"] = True
        data["v1512_climate_start_soc_margin_pct"] = CLIMATE_START_SOC_MARGIN_PCT
        data["v1512_climate_start_solar_cover_fraction"] = CLIMATE_SOLAR_COVER_FRACTION
        return data

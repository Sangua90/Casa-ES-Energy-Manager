"""Casa ES Energy Manager v1.5.17 battery-SOC hysteresis layer.

Each managed load keeps its configured min_battery_soc as the centre of a
stateful hysteresis band. With the default 5 percentage-point margin, a 40%
threshold releases automatic starts only at >=45% and keeps them released until
SOC falls to <=35%. This prevents morning start/stop chatter around one exact
SOC value while preserving all existing planner, priority and safety rules.
"""

from __future__ import annotations

from typing import Any

from .const import CONF_DEVICE_MIN_BATTERY_SOC
from .coordinator_v1515 import CasaESEnergyCoordinator as V1515Coordinator

BATTERY_SOC_HYSTERESIS_PCT = 5.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V1515Coordinator):
    """v1.5.17 coordinator with stable per-load battery SOC admission bands."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._battery_soc_load_released: dict[str, bool] = {}

    def _current_battery_soc(self) -> float | None:
        """Use the most recent coordinator value; one refresh of lag is harmless."""
        value = (self.data or {}).get("battery_soc")
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _managed_device_snapshots(self) -> list[dict[str, Any]]:
        devices = super()._managed_device_snapshots()
        soc = self._current_battery_soc()
        active_ids: set[str] = set()

        for item in devices:
            subentry_id = str(item.get("subentry_id") or "")
            if not subentry_id:
                continue
            active_ids.add(subentry_id)

            configured = max(
                min(_number(item.get(CONF_DEVICE_MIN_BATTERY_SOC), 0.0), 100.0),
                0.0,
            )
            lower = max(configured - BATTERY_SOC_HYSTERESIS_PCT, 0.0)
            upper = min(configured + BATTERY_SOC_HYSTERESIS_PCT, 100.0)

            released = self._battery_soc_load_released.get(subentry_id)
            if released is None:
                # Safe restart semantics: while inside the band, wait for the
                # upper boundary rather than immediately starting loads.
                released = bool(soc is not None and soc >= upper)
            elif soc is not None:
                if released and soc <= lower:
                    released = False
                elif not released and soc >= upper:
                    released = True

            self._battery_soc_load_released[subentry_id] = released
            item["configured_min_battery_soc"] = round(configured, 1)
            item["battery_soc_hysteresis_pct"] = BATTERY_SOC_HYSTERESIS_PCT
            item["battery_soc_hysteresis_lower_pct"] = round(lower, 1)
            item["battery_soc_hysteresis_upper_pct"] = round(upper, 1)
            item["battery_soc_hysteresis_released"] = released
            # Re-use the existing dry-run and stop machinery with a stateful
            # effective threshold. Released loads may remain available down to
            # the lower edge; blocked loads must reach the upper edge first.
            item[CONF_DEVICE_MIN_BATTERY_SOC] = lower if released else upper
            item["effective_min_battery_soc"] = round(
                item[CONF_DEVICE_MIN_BATTERY_SOC], 1
            )

        for subentry_id in set(self._battery_soc_load_released) - active_ids:
            self._battery_soc_load_released.pop(subentry_id, None)

        return devices

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v1517_battery_soc_hysteresis"] = {
            "enabled": True,
            "margin_pct": BATTERY_SOC_HYSTERESIS_PCT,
            "semantics": "configured_threshold_plus_minus_margin",
            "restart_inside_band": "battery_priority_until_upper_boundary",
            "states": {
                key: "loads_released" if value else "battery_priority"
                for key, value in sorted(self._battery_soc_load_released.items())
            },
        }
        return data

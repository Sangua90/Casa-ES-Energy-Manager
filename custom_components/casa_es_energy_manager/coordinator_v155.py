"""Casa ES Energy Manager v1.5.5 surplus and thermal-storage refinements.

v1.5.5 promotes the configured PV-potential/virtual sensor to a guarded solar
opportunity signal for small flexible loads, unifies the curtailment flag used
by generic and thermal control, and progressively fills DHW thermal storage
near the end of useful solar production instead of leaving clipped PV unused.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_NOMINAL_POWER_W,
    DEFAULT_BATTERY_TARGET_SOC,
)
from .coordinator_v154 import CasaESEnergyCoordinator as V154Coordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_NORMAL_MAX_TEMP_C,
)

VIRTUAL_SURPLUS_GRID_IMPORT_MAX_W = 100.0
VIRTUAL_SURPLUS_MIN_W = 100.0
VIRTUAL_SURPLUS_BATTERY_WINDOW_PCT = 3.0
THERMAL_RAMP_180_MIN_C = 5.0
THERMAL_RAMP_120_MIN_C = 3.0
THERMAL_FULL_TARGET_MINUTES = 60.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V154Coordinator):
    """v1.5.5 controller using guarded virtual surplus and solar-end DHW fill."""

    def _virtual_surplus_opportunity(self, data: dict[str, Any]) -> bool:
        """Return whether virtual/potential PV is safe enough to probe with loads."""
        target_soc = _number(
            self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC),
            DEFAULT_BATTERY_TARGET_SOC,
        )
        soc = _number(data.get("battery_soc"), 0.0)
        potential_after_house = max(
            _number(data.get("pv_potential_after_house_w"), 0.0), 0.0
        )
        policy = data.get("planner_policy") or {}
        return bool(
            potential_after_house >= VIRTUAL_SURPLUS_MIN_W
            and soc >= target_soc - VIRTUAL_SURPLUS_BATTERY_WINDOW_PCT
            and _number(data.get("grid_import_w"), 0.0)
            <= VIRTUAL_SURPLUS_GRID_IMPORT_MAX_W
            and not data.get("grid_warning")
            and not data.get("inverter_warning")
            and policy.get("target_reachability") != "definite_shortfall"
        )

    def _curtailment_harvest_available(self, data: dict[str, Any]) -> bool:
        """Allow v1.5.4 harvesting from either proven clipping or virtual surplus."""
        return bool(
            super()._curtailment_harvest_available(data)
            or self._virtual_surplus_opportunity(data)
        )

    def _thermal_target(
        self, item: dict[str, Any], data: dict[str, Any], now: Any
    ) -> tuple[float, str]:
        """Raise DHW target progressively as useful solar approaches its end."""
        target, reason = super()._thermal_target(item, data, now)
        normal_max = float(item.get(CONF_THERMAL_NORMAL_MAX_TEMP_C) or 65.0)
        target_soc = _number(
            self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC),
            DEFAULT_BATTERY_TARGET_SOC,
        )
        soc = _number(data.get("battery_soc"), 0.0)
        nominal = max(_number(item.get(CONF_DEVICE_NOMINAL_POWER_W), 0.0), 1.0)
        solar_available_w = max(
            _number(data.get("solar_after_house_w"), 0.0),
            _number(data.get("pv_potential_after_house_w"), 0.0),
        )

        # Keep battery-first semantics until the battery is essentially at its
        # requested target, and never invent thermal demand without enough PV
        # opportunity to run this appliance.
        if soc < target_soc - 1.0 or solar_available_w < nominal * 0.9:
            return target, reason

        window = self._dynamic_solar_window(now, data)
        solar_end = window.get("solar_useful_end") if window else None
        if solar_end is None:
            # No curve: if PV is visibly being clipped and the battery is ready,
            # use the configured normal thermal maximum rather than wasting it.
            if data.get("pv_curtailment_likely") or self._virtual_surplus_opportunity(data):
                raised = max(target, normal_max)
                if raised > target:
                    return round(raised, 1), f"{reason}; accumulo termico FV fino a {normal_max:.1f}°C"
            return target, reason

        minutes_left = max((solar_end - now).total_seconds() / 60.0, 0.0)
        desired = target
        stage = None
        if minutes_left <= THERMAL_FULL_TARGET_MINUTES:
            desired = max(desired, normal_max)
            stage = f"ultimo FV utile ({minutes_left:.0f} min): obiettivo {normal_max:.1f}°C"
        elif minutes_left <= 120.0:
            desired = max(desired, normal_max - THERMAL_RAMP_120_MIN_C)
            stage = f"FV utile in esaurimento ({minutes_left:.0f} min)"
        elif minutes_left <= 180.0:
            desired = max(desired, normal_max - THERMAL_RAMP_180_MIN_C)
            stage = f"precarica termica prima del tramonto ({minutes_left:.0f} min)"

        desired = min(desired, normal_max)
        if desired > target + 1e-9 and stage:
            return round(desired, 1), f"{reason}; {stage}"
        return target, reason

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        """Use one unified solar-opportunity flag throughout the control chain."""
        virtual = self._virtual_surplus_opportunity(data)
        unified = bool(data.get("pv_curtailment_likely") or virtual)
        data["curtailment_likely"] = unified
        data["v155_virtual_surplus_opportunity"] = virtual
        data["v155_unified_curtailment_signal"] = unified
        await super()._async_apply_real_control(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        virtual = self._virtual_surplus_opportunity(data)
        data["v155_virtual_surplus_opportunity"] = virtual
        data["v155_virtual_surplus_available_w"] = round(
            max(_number(data.get("pv_potential_after_house_w"), 0.0), 0.0), 1
        )
        data["v155_virtual_surplus_grid_import_max_w"] = VIRTUAL_SURPLUS_GRID_IMPORT_MAX_W
        data["v155_virtual_surplus_battery_window_pct"] = VIRTUAL_SURPLUS_BATTERY_WINDOW_PCT
        data["v155_unified_curtailment_signal"] = bool(
            data.get("pv_curtailment_likely") or virtual
        )
        data["v155_thermal_end_of_solar_ramp"] = {
            "180_min_target_below_normal_max_c": THERMAL_RAMP_180_MIN_C,
            "120_min_target_below_normal_max_c": THERMAL_RAMP_120_MIN_C,
            "60_min_target": "normal_max",
        }
        return data

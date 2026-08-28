"""Casa ES Energy Manager v1.5.6 battery trajectory and priority fixes.

v1.5.6 makes the 3.5 kW battery acceptance limit explicit, reserves only the
power actually required to stay on the battery-target trajectory, releases PV
above that reservation to flexible loads, compares thermal-storage priority
with generic/climate starts, and makes phase-only warnings advisory throughout
the real-control call chain.
"""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_ENABLED,
    CONF_DEVICE_MIN_BATTERY_SOC,
    CONF_DEVICE_NOMINAL_POWER_W,
    CONF_DEVICE_TYPE,
    DEFAULT_BATTERY_TARGET_SOC,
    DEVICE_MODE_AUTO,
)
from .coordinator_v155 import CasaESEnergyCoordinator as V155Coordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_BASE_TEMP_C,
    CONF_THERMAL_BOOST_ENTITY,
    CONF_THERMAL_LEGIONELLA_ENTITY,
    DEVICE_TYPE_THERMAL,
)

BATTERY_MAX_CHARGE_W = 3500.0
BATTERY_TRAJECTORY_MARGIN_W = 150.0
BATTERY_RECOVERY_TRIGGER_FRACTION = 0.90
BATTERY_DISCHARGE_RECOVERY_W = 100.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V155Coordinator):
    """v1.5.6 coordinator with explicit battery-cap allocation and priorities."""

    def _battery_allocation(self, data: dict[str, Any]) -> dict[str, Any]:
        policy = data.get("planner_policy") or {}
        target_soc = _number(
            self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC),
            DEFAULT_BATTERY_TARGET_SOC,
        )
        soc = _number(data.get("battery_soc"), 0.0)
        potential_after_house = max(
            _number(data.get("pv_potential_after_house_w"), 0.0), 0.0
        )
        measured_after_house = max(_number(data.get("solar_after_house_w"), 0.0), 0.0)
        hours = max(_number(policy.get("hours_to_target"), 0.0), 0.0)
        needed_kwh = max(_number(policy.get("battery_input_energy_needed_kwh"), 0.0), 0.0)

        target_active = bool(policy.get("battery_target_active", soc < target_soc))
        below_target = target_active and soc < target_soc - 0.1 and needed_kwh > 0.0
        raw_required_w = 0.0
        if below_target and hours > 1e-6:
            raw_required_w = needed_kwh * 1000.0 / hours
        elif below_target:
            raw_required_w = BATTERY_MAX_CHARGE_W

        required_w = min(max(raw_required_w, 0.0), BATTERY_MAX_CHARGE_W)
        # A small margin prevents a 5-second fluctuation from stealing power that
        # the battery trajectory genuinely needs, without hiding large overflow.
        reserved_w = min(
            BATTERY_MAX_CHARGE_W,
            required_w + (BATTERY_TRAJECTORY_MARGIN_W if required_w > 0 else 0.0),
        )
        overflow_w = max(potential_after_house - reserved_w, 0.0)
        measured_overflow_w = max(measured_after_house - reserved_w, 0.0)

        hard_recovery = bool(
            below_target
            and (
                raw_required_w >= BATTERY_MAX_CHARGE_W * BATTERY_RECOVERY_TRIGGER_FRACTION
                or policy.get("target_reachability") == "definite_shortfall"
                or (
                    _number(data.get("battery_discharge_w"), 0.0)
                    > BATTERY_DISCHARGE_RECOVERY_W
                    and potential_after_house > 0.0
                )
            )
        )
        return {
            "target_soc": round(target_soc, 1),
            "below_target": below_target,
            "raw_required_w": round(raw_required_w, 1),
            "required_w": round(required_w, 1),
            "reserved_w": round(reserved_w, 1),
            "overflow_w": round(overflow_w, 1),
            "measured_overflow_w": round(measured_overflow_w, 1),
            "hard_recovery": hard_recovery,
        }

    def _apply_battery_trajectory_guard(self, data: dict[str, Any]) -> None:
        allocation = self._battery_allocation(data)
        data["v156_battery_allocation"] = allocation
        remaining_overflow = _number(allocation.get("overflow_w"), 0.0)
        hard_recovery = bool(allocation.get("hard_recovery"))

        decisions = list(data.get("dry_run_decisions") or [])
        # Spend any true overflow strictly by configured priority. A lower number
        # is a higher priority and must always get first refusal.
        for decision in sorted(
            decisions, key=lambda item: int(item.get("priority") or 50)
        ):
            if decision.get("management_mode") != DEVICE_MODE_AUTO:
                continue
            if decision.get("entity_active"):
                continue
            power_w = max(
                _number(
                    decision.get("admission_power_w"),
                    decision.get("nominal_power_w", 0.0),
                ),
                0.0,
            )
            if power_w <= 0:
                continue
            decision_kind = str(decision.get("decision") or "")
            reason = str(decision.get("reason") or "")
            energy_block = decision_kind == "waiting_energy" or "Margine batteria" in reason
            if not energy_block:
                continue
            if power_w <= remaining_overflow + 1e-9:
                decision["would_start"] = True
                decision["decision"] = "battery_cap_overflow_start"
                decision["reason"] = (
                    "FV oltre la potenza riservata alla traiettoria batteria: "
                    "avvio secondo priorità senza ridurre la ricarica richiesta."
                )
                remaining_overflow = max(remaining_overflow - power_w, 0.0)

        # If the battery has fallen badly behind, stop lower-value flexible loads
        # as soon as their real minimum-ON protection allows it. The base
        # controller already sheds the numerically highest priority value first.
        if hard_recovery:
            for decision in decisions:
                if decision.get("management_mode") != DEVICE_MODE_AUTO:
                    continue
                if not decision.get("entity_active") or decision.get("stop_is_hard_safety"):
                    continue
                if not decision.get("can_auto_stop"):
                    continue
                decision["would_stop"] = True
                decision["decision"] = "battery_trajectory_recovery_stop"
                decision["reason"] = (
                    "Batteria fuori traiettoria verso il target: libero potenza "
                    "rispettando il minimo ON reale del dispositivo."
                )

        data["v156_flexible_overflow_remaining_w"] = round(remaining_overflow, 1)

    def _thermal_action_priority(self, data: dict[str, Any], now: Any) -> int | None:
        """Return priority of a thermal command that is genuinely actionable now."""
        allocation = data.get("v156_battery_allocation") or self._battery_allocation(data)
        overflow = _number(allocation.get("overflow_w"), 0.0)
        below_target = bool(allocation.get("below_target"))

        candidates: list[int] = []
        for raw in data.get("managed_device_configs") or []:
            if str(raw.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_THERMAL:
                continue
            item = self._thermal_context(dict(raw))
            if not bool(item.get(CONF_DEVICE_ENABLED, True)):
                continue
            if str(item.get("management_mode") or DEVICE_MODE_AUTO) != DEVICE_MODE_AUTO:
                continue
            if item.get("thermal_legionella_active"):
                continue
            subentry_id = str(item.get("subentry_id") or "")
            if not subentry_id:
                continue

            # Owned Boost cleanup is actionable regardless of start priority.
            if subentry_id in self._thermal_boost_owned:
                target = _number(
                    self._thermal_target_c.get(subentry_id),
                    _number(item.get(CONF_THERMAL_BASE_TEMP_C), 52.0),
                )
                temp = item.get("thermal_current_temperature_c")
                if temp is not None and _number(temp) >= target - 0.3:
                    candidates.append(int(item.get("priority") or 50))
                continue

            if item.get("thermal_boost_active") and not item.get(
                "thermal_boost_owned_by_casa_es"
            ):
                continue
            temp = item.get("thermal_current_temperature_c")
            if temp is None:
                continue
            base = _number(item.get(CONF_THERMAL_BASE_TEMP_C), 52.0)
            if _number(temp) < base - 0.5:
                continue
            if _number(data.get("battery_soc"), 0.0) < _number(
                item.get(CONF_DEVICE_MIN_BATTERY_SOC), 0.0
            ):
                continue
            nominal = max(_number(item.get(CONF_DEVICE_NOMINAL_POWER_W), 0.0), 1.0)
            # Below target the boiler may only consume the part of PV that cannot
            # be needed by the battery trajectory. At/above target normal v1.5.5
            # solar-opportunity semantics apply.
            if below_target and overflow < nominal * 0.9:
                continue
            if not below_target:
                surplus = max(
                    _number(data.get("solar_after_house_w"), 0.0),
                    _number(data.get("pv_potential_after_house_w"), 0.0),
                )
                if surplus < nominal * 0.9 and not bool(
                    data.get("v155_unified_curtailment_signal")
                ):
                    continue
            target, _ = self._thermal_target(item, data, now)
            if target > _number(temp) + 0.5:
                candidates.append(int(item.get("priority") or 50))

        return min(candidates) if candidates else None

    def _enforce_cross_type_priority(self, data: dict[str, Any], now: Any) -> None:
        thermal_priority = self._thermal_action_priority(data, now)
        data["v156_thermal_action_priority"] = thermal_priority
        if thermal_priority is None:
            return
        # coordinator_v15 executes generic/climate commands before thermal ones.
        # Suppress only lower-priority starts so the thermal action gets its fair
        # place in the same global priority queue. Higher-priority generic loads
        # remain untouched.
        for decision in data.get("dry_run_decisions") or []:
            if not decision.get("would_start"):
                continue
            if int(decision.get("priority") or 50) <= thermal_priority:
                continue
            decision["would_start"] = False
            decision["decision"] = "waiting_higher_priority_thermal"
            decision["reason"] = (
                "Attendo un accumulo termico con priorità superiore prima di "
                "avviare questo carico."
            )

    async def _async_apply_thermal_control(
        self, data: dict[str, Any], now: Any
    ) -> bool:
        """Make thermal control respect battery-reserved power below target."""
        allocation = data.get("v156_battery_allocation") or self._battery_allocation(data)
        if not bool(allocation.get("below_target")):
            return await super()._async_apply_thermal_control(data, now)

        original_potential = data.get("pv_potential_after_house_w")
        original_measured = data.get("solar_after_house_w")
        original_curtailment = data.get("curtailment_likely")
        try:
            data["pv_potential_after_house_w"] = _number(allocation.get("overflow_w"), 0.0)
            data["solar_after_house_w"] = _number(
                allocation.get("measured_overflow_w"), 0.0
            )
            data["curtailment_likely"] = False
            return await super()._async_apply_thermal_control(data, now)
        finally:
            data["pv_potential_after_house_w"] = original_potential
            data["solar_after_house_w"] = original_measured
            data["curtailment_likely"] = original_curtailment

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        """Make phase-only warning advisory even through legacy v1.4 branches."""
        self._apply_battery_trajectory_guard(data)
        self._enforce_cross_type_priority(data, now)

        raw_phase_warning = bool(data.get("phase_warning"))
        data["v156_phase_warning_raw"] = raw_phase_warning
        data["v156_phase_warning_control_mode"] = "advisory_only"
        try:
            # v1.4 contains direct `data[phase_warning]` branches that bypass the
            # later _electrical_warning_active override. Mask it only while real
            # control executes; diagnostics/headroom keep the raw value.
            data["phase_warning"] = False
            await super()._async_apply_real_control(data, now)
        finally:
            data["phase_warning"] = raw_phase_warning

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        allocation = self._battery_allocation(data)
        data["v156_battery_max_charge_w"] = BATTERY_MAX_CHARGE_W
        data["v156_battery_allocation"] = allocation
        data["v156_battery_trajectory_mode"] = (
            "hard_recovery" if allocation["hard_recovery"] else "trajectory_reserve"
        )
        data["v156_priority_semantics"] = "lower_number_higher_priority_global"
        data["v156_phase_only_shed"] = "disabled_all_control_paths"
        return data

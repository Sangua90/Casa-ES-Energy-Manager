"""v1.4.1 coordinator: adaptive dual-source PV potential estimation."""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
    CONF_BATTERY_TARGET_SOC,
    CONF_ENERGY_PREFERENCE,
    CONF_EXPECTED_BASE_LOAD_W,
    CONF_INVERTER_POWER_LIMIT,
    CURTAILMENT_GRID_IMPORT_MAX_W,
    CURTAILMENT_POTENTIAL_GAP_W,
    CURTAILMENT_SOC_THRESHOLD,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_ENERGY_PREFERENCE,
    DEFAULT_EXPECTED_BASE_LOAD_W,
    DEFAULT_INVERTER_POWER_LIMIT,
)
from .coordinator_v14 import CasaESEnergyCoordinator as V14Coordinator
from .device_dry_run_v1 import evaluate_managed_devices
from .planner_policy_v1 import build_planner_policy
from .pv_estimator_v141 import AdaptivePVPotentialEstimator


class CasaESEnergyCoordinator(V14Coordinator):
    """v1.4.1 controller with zero-export-aware PV potential estimation."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self.pv_estimator = AdaptivePVPotentialEstimator()
        self._defer_real_control_for_pv_estimate = False

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        """Defer commands until the v1.4.1 PV estimate has been applied.

        v1.2 normally evaluates and executes load control inside its update.  The
        v1.4.1 layer must first replace the single-source PV potential with the
        fused estimate and rebuild the deterministic decisions.  During the base
        pass we therefore collect data only; one real-control pass is executed at
        the end with the corrected solar opportunity.
        """
        if self._defer_real_control_for_pv_estimate:
            self._write_emergency_diagnostics(data, now)
            self._write_real_control_diagnostics(data, "pv_estimate_pending")
            return
        await super()._async_apply_real_control(data, now)

    def _apply_pv_estimate(self, data: dict[str, Any]) -> None:
        custom = data.get("pv_potential_input_w")
        provider = data.get("forecast_current_hour_power_w")

        estimate = self.pv_estimator.update(
            measured_pv_w=float(data.get("pv_power_w") or 0.0),
            load_w=float(data.get("load_power_w") or 0.0),
            grid_power_w=float(data.get("grid_power_w") or 0.0),
            battery_power_w=float(data.get("battery_power_w") or 0.0),
            custom_forecast_w=(float(custom) if custom is not None else None),
            provider_forecast_w=(float(provider) if provider is not None else None),
            inverter_limit_w=float(
                self._config(CONF_INVERTER_POWER_LIMIT, DEFAULT_INVERTER_POWER_LIMIT)
            ),
        )
        data.update(estimate)

        battery_soc = data.get("battery_soc")
        grid_import = max(float(data.get("grid_power_w") or 0.0), 0.0)
        data["pv_curtailment_likely"] = bool(
            battery_soc is not None
            and float(battery_soc) >= CURTAILMENT_SOC_THRESHOLD
            and grid_import <= CURTAILMENT_GRID_IMPORT_MAX_W
            and float(data.get("pv_potential_gap_w") or 0.0) >= CURTAILMENT_POTENTIAL_GAP_W
        )

        # Preserve hard electrical warnings.  Only the informational monitoring
        # status may be replaced by the newly detected curtailment state.
        if not (
            data.get("grid_warning")
            or data.get("phase_warning")
            or data.get("inverter_warning")
        ):
            data["status"] = (
                "pv_curtailment_likely"
                if data["pv_curtailment_likely"]
                else "monitoring"
            )

    def _rebuild_solar_dependent_decisions(self, data: dict[str, Any], now: Any) -> None:
        """Re-run deterministic planning after replacing PV potential."""
        _, target = self._target_time()
        policy = build_planner_policy(
            data,
            now=now,
            target=target,
            battery_capacity_kwh=float(
                self._config(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
            ),
            battery_target_soc=float(
                self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC)
            ),
            expected_base_load_w=float(
                self._config(CONF_EXPECTED_BASE_LOAD_W, DEFAULT_EXPECTED_BASE_LOAD_W)
            ),
            battery_charge_efficiency_pct=float(
                self._config(
                    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
                    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
                )
            ),
            energy_preference=str(
                self._config(CONF_ENERGY_PREFERENCE, DEFAULT_ENERGY_PREFERENCE)
            ),
        )

        devices = list(data.get("managed_device_configs") or [])
        data["planner_policy"] = policy
        data.update(
            evaluate_managed_devices(devices, data=data, policy=policy, now=now)
        )
        data["energy_preference"] = policy["energy_preference"]

        for key in (
            "battery_energy_needed_kwh",
            "battery_input_energy_needed_kwh",
            "base_load_energy_to_target_kwh",
            "forecast_energy_to_target_kwh",
            "forecast_margin_before_base_load_kwh",
            "forecast_margin_after_base_load_kwh",
            "flexible_energy_budget_kwh",
        ):
            data[key] = policy.get(key)
        data["planner_target_reachability"] = policy.get("target_reachability")
        data["planner_grid_pressure"] = policy.get("grid_pressure")
        data["planner_solar_state"] = policy.get("solar_state")

    async def _async_update_data(self) -> dict[str, Any]:
        self._defer_real_control_for_pv_estimate = True
        try:
            data = await super()._async_update_data()
        finally:
            self._defer_real_control_for_pv_estimate = False

        now, _ = self._target_time()
        self._apply_pv_estimate(data)
        self._rebuild_solar_dependent_decisions(data, now)

        # Exactly one command pass, now based on the fused PV estimate.
        await super()._async_apply_real_control(data, now)
        return data

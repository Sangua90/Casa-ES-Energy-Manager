"""v1.4.3 coordinator: solar-bounded daily target recovery."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .const import CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR
from .coordinator_v142 import CasaESEnergyCoordinator as V142Coordinator
from .daily_target import (
    TARGET_MODE_DAY_COMPLETE,
    TARGET_MODE_DEADLINE,
    TARGET_MODE_RECOVERY,
    daily_battery_target_window,
    solar_recovery_available,
)


class CasaESEnergyCoordinator(V142Coordinator):
    """v1.4.3 controller with same-day recovery bounded by solar opportunity."""

    def _target_window(
        self,
        now: Any | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = now or dt_util.now()
        target_hour = int(
            self._config(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR)
        )
        source = data if data is not None else (self.data or {})
        return daily_battery_target_window(
            current,
            target_hour,
            recovery_solar_available=solar_recovery_available(source),
        )

    def _target_time(self) -> tuple[Any, Any]:
        now = dt_util.now()
        window = self._target_window(now)
        return now, window["planning_target"]

    @staticmethod
    def _apply_target_mode_to_policy(
        policy: dict[str, Any],
        window: dict[str, Any],
    ) -> None:
        mode = window["mode"]
        policy["battery_target_mode"] = mode
        policy["battery_target_deadline"] = window["deadline"].isoformat()
        policy["battery_target_effective_planning_target"] = window[
            "planning_target"
        ].isoformat()
        policy["battery_target_active"] = bool(window["target_active"])

        # Post-deadline recovery is solar-only. The daily target must never turn
        # into a recommendation to charge from Enel after the configured hour.
        if mode != TARGET_MODE_DEADLINE:
            policy["grid_charge_allowed"] = False

        if mode == TARGET_MODE_DAY_COMPLETE:
            # The useful solar opportunity for today is over. Keep SOC deficit as
            # diagnostic information, but stop treating it as an active shortfall.
            policy["target_reachability"] = "day_complete"
            policy["battery_first_preferred"] = False
            policy["flexible_energy_budget_kwh"] = 0.0
            policy["fallback_strategy"] = "balanced"

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()

        # Re-evaluate using the freshly collected snapshot. This closes recovery
        # immediately when the forecast and real PV both show that the solar day
        # is over, rather than waiting for the next coordinator refresh.
        now = dt_util.now()
        window = self._target_window(now, data)
        mode = window["mode"]

        data["battery_target_mode"] = mode
        data["battery_target_deadline"] = window["deadline"].isoformat()
        data["battery_target_effective_planning_target"] = window[
            "planning_target"
        ].isoformat()
        data["battery_target_active"] = bool(window["target_active"])
        data["battery_target_recovery_with_solar"] = mode == TARGET_MODE_RECOVERY
        data["battery_target_day_complete"] = mode == TARGET_MODE_DAY_COMPLETE
        # Legacy diagnostic field retained so old dashboards do not break.
        data["battery_target_recovery_until_midnight"] = mode == TARGET_MODE_RECOVERY

        policy = data.get("planner_policy")
        if isinstance(policy, dict):
            self._apply_target_mode_to_policy(policy, window)
            data["planner_target_reachability"] = policy.get("target_reachability")

        return data

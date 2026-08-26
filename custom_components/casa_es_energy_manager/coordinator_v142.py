"""v1.4.2 coordinator: daily target recovery and switch auto-resume."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_TARGET_HOUR,
    CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
    CONF_MONITORED_LOAD_RESUME_ENTITY,
    DEFAULT_BATTERY_TARGET_HOUR,
)
from .coordinator_v141 import CasaESEnergyCoordinator as V141Coordinator
from .daily_target import daily_battery_target_window
from .monitored_control_semantics import effective_resume_entity


class CasaESEnergyCoordinator(V141Coordinator):
    """v1.4.2 controller with same-day target recovery until midnight."""

    def _target_window(self, now: Any | None = None) -> dict[str, Any]:
        current = now or dt_util.now()
        target_hour = int(
            self._config(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR)
        )
        return daily_battery_target_window(current, target_hour)

    def _target_time(self) -> tuple[Any, Any]:
        """Return the effective battery planning horizon for the current day.

        The configured hour remains the desired deadline. After that deadline,
        Casa ES does not jump to tomorrow: it keeps the same SOC target active
        until midnight so residual solar can recover the battery.
        """
        now = dt_util.now()
        window = self._target_window(now)
        return now, window["planning_target"]

    @staticmethod
    def _load_with_effective_resume(load: dict[str, Any]) -> dict[str, Any]:
        """Inject an inferred resume command when emergency control is a switch."""
        item = dict(load)
        resume, source = effective_resume_entity(
            item.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY),
            item.get(CONF_MONITORED_LOAD_RESUME_ENTITY),
        )
        if resume:
            item[CONF_MONITORED_LOAD_RESUME_ENTITY] = resume
        item["effective_resume_entity"] = resume or None
        item["resume_semantics"] = source
        item["auto_resume_same_switch"] = source == "same_switch"
        return item

    def _data_with_effective_resumes(self, data: dict[str, Any]) -> dict[str, Any]:
        patched = dict(data)
        patched["monitored_loads"] = [
            self._load_with_effective_resume(item)
            for item in (data.get("monitored_loads") or [])
        ]
        return patched

    def _refresh_shed_records(self, data: dict[str, Any]) -> None:
        """Keep inferred switch resume commands when refreshing shed records."""
        super()._refresh_shed_records(self._data_with_effective_resumes(data))

    async def _async_execute_monitored_shed(
        self,
        load: dict[str, Any],
        *,
        reason: str,
        now: Any,
        data: dict[str, Any],
    ) -> bool:
        """Store the same switch as resume command when no separate one exists."""
        return await super()._async_execute_monitored_shed(
            self._load_with_effective_resume(load),
            reason=reason,
            now=now,
            data=data,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()

        now = dt_util.now()
        window = self._target_window(now)
        data["battery_target_mode"] = window["mode"]
        data["battery_target_deadline"] = window["deadline"].isoformat()
        data["battery_target_effective_planning_target"] = window[
            "planning_target"
        ].isoformat()
        data["battery_target_recovery_until_midnight"] = (
            window["mode"] == "recovery_until_midnight"
        )

        # Diagnostics/UI context can show that a single switch is sufficient even
        # though the persisted subentry still contains only the emergency field.
        data["monitored_loads"] = [
            self._load_with_effective_resume(item)
            for item in (data.get("monitored_loads") or [])
        ]

        policy = data.get("planner_policy")
        if isinstance(policy, dict):
            policy["battery_target_mode"] = window["mode"]
            policy["battery_target_deadline"] = window["deadline"].isoformat()
            policy["battery_target_effective_planning_target"] = window[
                "planning_target"
            ].isoformat()
        return data

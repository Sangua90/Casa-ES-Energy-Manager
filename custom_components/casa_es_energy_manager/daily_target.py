"""Pure helpers for the Casa ES daily battery target window."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .const import (
    BATTERY_RECOVERY_FORECAST_REMAINING_MIN_KWH,
    BATTERY_RECOVERY_MEASURED_PV_MIN_W,
    BATTERY_RECOVERY_POTENTIAL_PV_MIN_W,
)

TARGET_MODE_DEADLINE = "deadline"
TARGET_MODE_RECOVERY = "recovery_with_solar"
TARGET_MODE_DAY_COMPLETE = "day_complete_no_solar"


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def solar_recovery_available(data: dict[str, Any] | None) -> bool:
    """Return whether meaningful same-day solar opportunity still exists.

    Current PV keeps recovery active when it is genuinely useful. A temporary
    cloud does not prematurely close the day if potential PV or the remaining
    forecast still indicates useful production later.
    """
    source = data or {}
    measured = _float(source.get("pv_power_w"))
    if measured is None:
        measured = _float(source.get("pv_measured_power_w"))
    potential = _float(source.get("pv_potential_w"))
    remaining = _float(source.get("forecast_remaining_kwh"))

    return bool(
        (measured is not None and measured >= BATTERY_RECOVERY_MEASURED_PV_MIN_W)
        or (potential is not None and potential >= BATTERY_RECOVERY_POTENTIAL_PV_MIN_W)
        or (
            remaining is not None
            and remaining >= BATTERY_RECOVERY_FORECAST_REMAINING_MIN_KWH
        )
    )


def daily_battery_target_window(
    now: datetime,
    target_hour: int,
    *,
    recovery_solar_available: bool = True,
) -> dict[str, Any]:
    """Return today's configured deadline and effective planning horizon.

    Before the configured target hour Casa ES plans toward that deadline. After
    the deadline the same SOC target remains active only while meaningful solar
    opportunity still exists. Once today's useful PV opportunity has ended the
    daily recovery closes: Casa ES no longer chases 100% from the grid or keeps
    a battery shortfall active through the night. At midnight the new daily
    cycle naturally starts and again targets the configured hour.
    """
    hour = max(0, min(int(target_hour), 23))
    deadline = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    if now < deadline:
        return {
            "deadline": deadline,
            "planning_target": deadline,
            "mode": TARGET_MODE_DEADLINE,
            "target_active": True,
        }

    if recovery_solar_available:
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return {
            "deadline": deadline,
            "planning_target": midnight,
            "mode": TARGET_MODE_RECOVERY,
            "target_active": True,
        }

    return {
        "deadline": deadline,
        "planning_target": now,
        "mode": TARGET_MODE_DAY_COMPLETE,
        "target_active": False,
    }

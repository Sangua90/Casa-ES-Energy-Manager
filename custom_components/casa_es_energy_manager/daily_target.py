"""Pure helpers for the Casa ES daily battery target window."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

TARGET_MODE_DEADLINE = "deadline"
TARGET_MODE_RECOVERY = "recovery_until_midnight"


def daily_battery_target_window(now: datetime, target_hour: int) -> dict[str, Any]:
    """Return today's configured deadline and the effective planning horizon.

    Before the configured target hour, Casa ES plans to reach the target by that
    hour. After the deadline, the same daily SOC target remains active until
    midnight so remaining solar can recover a missed target or refill energy
    used later in the afternoon. At midnight a new daily cycle starts and the
    effective target becomes the configured hour of the new day.
    """
    hour = max(0, min(int(target_hour), 23))
    deadline = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    if now < deadline:
        return {
            "deadline": deadline,
            "planning_target": deadline,
            "mode": TARGET_MODE_DEADLINE,
        }

    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return {
        "deadline": deadline,
        "planning_target": midnight,
        "mode": TARGET_MODE_RECOVERY,
    }

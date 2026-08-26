"""PV-first scheduling helpers for managed loads with a daily minimum runtime."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

DAILY_MINIMUM_DEADLINE_RESERVE_MINUTES = 30.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value: Any) -> time | None:
    if value in (None, ""):
        return None
    raw = str(value)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def minutes_until_daily_deadline(now: datetime, end_before: Any = None) -> float:
    """Return usable minutes remaining before a device's daily deadline."""
    end = _parse_time(end_before)
    if end is None:
        deadline = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=now.tzinfo)
    else:
        deadline = datetime.combine(now.date(), end, tzinfo=now.tzinfo)
        if deadline <= now:
            return 0.0
    return max((deadline - now).total_seconds() / 60.0, 0.0)


def should_defer_daily_minimum_start(
    *,
    now: datetime,
    remaining_minimum_minutes: float,
    nominal_power_w: float,
    solar_after_house_w: float,
    pv_potential_after_house_w: float,
    end_before: Any = None,
    reserve_minutes: float = DAILY_MINIMUM_DEADLINE_RESERVE_MINUTES,
) -> tuple[bool, str, bool]:
    """Decide whether a daily-minimum load should wait for solar.

    Returns ``(defer, reason, deadline_pressure)``. A device is deferred while
    there is enough time left in the day to complete its remaining minimum later.
    If current surplus/potential solar can already cover the load, it may run now.
    As the daily deadline approaches, the deferment is released so the normal
    device rules decide whether battery/grid fallback is permitted.
    """
    remaining = max(_number(remaining_minimum_minutes), 0.0)
    nominal = max(_number(nominal_power_w), 0.0)
    if remaining <= 0 or nominal <= 0:
        return False, "", False

    solar_opportunity = max(
        _number(solar_after_house_w),
        _number(pv_potential_after_house_w),
        0.0,
    )
    if solar_opportunity + 1e-9 >= nominal:
        return False, "FV disponibile ora per coprire il carico minimo giornaliero.", False

    minutes_left = minutes_until_daily_deadline(now, end_before)
    reserve = max(_number(reserve_minutes), 0.0)
    deadline_pressure = remaining + reserve >= minutes_left - 1e-9
    if deadline_pressure:
        return (
            False,
            "Tempo residuo ridotto: il minimo giornaliero deve poter essere completato entro la fine della finestra.",
            True,
        )

    return (
        True,
        "Minimo giornaliero pianificato più avanti: Casa ES attende FV utile prima di usare batteria o rete.",
        False,
    )

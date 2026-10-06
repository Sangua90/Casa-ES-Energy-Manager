"""Pure DHW history reconstruction and conservative, explainable daily plans.

Inputs are full recorder states (attributes included), with aware timestamps.
Temperature drops are equivalent degrees, not litres or electrical kWh.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from math import isfinite
from statistics import median
from typing import Any


def number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def temperature(value: Any, unit: str) -> float | None:
    result = number(value)
    if result is None:
        return None
    if unit in ("°F", "F"):
        result = (result - 32) * 5 / 9
    elif unit not in ("°C", "C"):
        return None
    return result if 0 <= result <= 90 else None


def timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return result if result.tzinfo is not None else None


def reconstruct(history: dict[str, list[dict]], entities: dict[str, str],
                now: datetime, unit: str = "°C") -> dict:
    """Align independent state streams in UTC, then group draws in HA local time.

    Heating transitions, missing data, legionella and coarse passive cooling do
    not become synthetic showers. Negative movement while heating is a lower
    bound on draw; positive heating can mask a draw and is not inverted blindly.
    """
    streams = {}
    for role, entity in entities.items():
        rows = []
        for row in history.get(entity, []):
            at = timestamp(row.get("last_updated"))
            if at is not None and at <= now:
                rows.append((at.timestamp(), row))
        streams[role] = sorted(rows, key=lambda x: x[0])
    water = streams.get("water", [])
    times = {key: [x[0] for x in rows] for key, rows in streams.items()}

    def flag(role: str, at: float) -> bool | None:
        rows = streams.get(role, [])
        index = bisect_right(times.get(role, []), at) - 1
        if index < 0:
            return None
        state = str(rows[index][1].get("state", "")).lower()
        if state in ("on", "heating", "true", "1"):
            return True
        if state in ("off", "false", "0"):
            return False
        return None

    raw = []
    invalid_days = set()
    coverage: dict[str, float] = {}
    baseline = None
    last_seen = None
    last_valid = False
    for at, row in water:
        local = datetime.fromtimestamp(at, now.tzinfo)
        attrs = row.get("attributes") or {}
        temp = temperature(attrs.get("current_temperature"), unit)
        flags = tuple(flag(role, at) for role in ("heating", "boost", "legionella"))
        valid = temp is not None and row.get("state") not in ("unknown", "unavailable", "off") and None not in flags
        # Recorder stores state changes, not heartbeats: an unchanged overnight
        # state is not a telemetry outage. Count known-state coverage explicitly.
        if last_seen is not None and last_valid:
            cursor = last_seen
            while cursor < at:
                start = datetime.fromtimestamp(cursor, now.tzinfo)
                midnight = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                stop = min(at, midnight.timestamp())
                key = start.date().isoformat()
                coverage[key] = coverage.get(key, 0) + stop - cursor
                cursor = stop
        last_seen = at
        last_valid = valid
        if not valid or flags[2]:
            baseline = None
            continue
        # A transition inside a temperature interval invalidates that interval,
        # even when the two endpoints happen to have the same operating state.
        transition = baseline is not None and any(
            bisect_right(times.get(role, []), at) > bisect_right(times.get(role, []), baseline[0])
            and any(str(r[1].get("state")) != str(streams[role][i - 1][1].get("state"))
                    for i, r in enumerate(streams[role]) if i > 0 and baseline[0] < r[0] <= at)
            for role in ("heating", "boost", "legionella")
        )
        if baseline is None or baseline[2] != flags or transition:
            baseline = (at, temp, flags)
            continue
        delta = temp - baseline[1]
        if abs(delta) < 0.01:
            if at - baseline[0] > 6 * 3600:
                baseline = (at, temp, flags)
            continue
        hours = (at - baseline[0]) / 3600
        baseline = (at, temp, flags)
        if 1 / 60 <= hours <= 6:
            raw.append((local, delta, hours, flags, str(row.get("state", "")), temp))

    passive = [-delta / hours for _, delta, hours, flags, _, _ in raw
               if delta < 0 and not flags[0] and not flags[1] and -delta / hours <= 2]
    standby = min(median(passive), 2.0) if passive else 0.5
    gains = [delta / hours for _, delta, hours, flags, mode, temp in raw
             if delta > 0 and flags[0] and not flags[1] and mode.upper() == "GREEN"
             and temp <= 53 and 0.5 <= delta / hours <= 10]
    boost_gains = sorted(delta / hours for _, delta, hours, flags, _, _ in raw
                         if delta > 0 and flags[1] and 1 <= delta / hours <= 20)
    draws: dict[str, dict[str, float]] = {}
    for local, delta, hours, flags, _, _ in raw:
        if delta >= 0 or flags[1]:
            continue
        # Coarse slow drops have insufficient evidence for a draw.
        if not flags[0] and -delta / hours <= 2:
            continue
        drop = max(-delta - standby * hours, 0)
        if drop < 0.4:
            continue
        bucket = draws.setdefault(local.date().isoformat(), {})
        hour = str(local.hour)
        bucket[hour] = bucket.get(hour, 0) + drop

    first_day = datetime.fromtimestamp(water[0][0], now.tzinfo).date() if water else now.date()
    cutoff = now.date() - timedelta(days=30)
    for day, seconds in coverage.items():
        midnight = datetime.fromisoformat(day).replace(tzinfo=now.tzinfo)
        day_seconds = (midnight + timedelta(days=1)).timestamp() - midnight.timestamp()
        if seconds < day_seconds * 0.8:
            invalid_days.add(day)
    complete = {day: buckets for day, buckets in draws.items()
                if max(first_day, cutoff) <= datetime.fromisoformat(day).date() < now.date()
                and day not in invalid_days and day in coverage}
    return {"draw_by_day": complete, "today_draw": draws.get(now.date().isoformat(), {}),
            "standby_loss_c_per_h": round(standby, 3),
            "green_c_per_h": round(median(gains), 3) if len(gains) >= 3 else 2.0,
            "green_rate_samples": len(gains), "completed_observed_days": len(complete),
            "boost_c_per_h": round(min(boost_gains[(len(boost_gains) - 1) // 4], 12), 3) if len(boost_gains) >= 6 else 4.0,
            "boost_rate_samples": len(boost_gains),
            "invalid_days": sorted(invalid_days), "masked_draws_possible": True,
            "source": "home_assistant_recorder", "updated_at": now.isoformat()}


def forecast(model: dict, day: datetime) -> tuple[list[float], float]:
    """Use completed demand days; same weekday when at least two are available."""
    days = model.get("draw_by_day", {})
    matching = {d: v for d, v in days.items()
                if datetime.fromisoformat(d).weekday() == day.weekday()}
    chosen = matching if len(matching) >= 2 else days
    hourly = [0.0] * 24
    total_weight = 0.0
    totals = []
    for date, buckets in chosen.items():
        age = (day.date() - datetime.fromisoformat(date).date()).days
        weight = 1.0 if age <= 7 else 0.75 if age <= 14 else 0.5 if age <= 21 else 0.3
        values = [max(number(buckets.get(str(h)), 0.0), 0) for h in range(24)]
        totals.append(sum(values))
        for h in range(24):
            hourly[h] += values[h] * weight
        total_weight += weight
    if total_weight:
        hourly = [x / total_weight for x in hourly]
    else:
        # Explicit bootstrap, never advertised as learned household demand.
        hourly[7], hourly[18] = 3.0, 6.0
    # Positive historical residuals adapt the reserve; keep a 4.5 C floor.
    # Unexpected visitors/showers need evidence from all days, not only a small
    # same-weekday sample that can hide recent high-demand evenings.
    observed_totals = [sum(max(number(v, 0), 0) for v in buckets.values())
                       for buckets in days.values()]
    errors = sorted(max(x - sum(hourly), 0) for x in observed_totals)
    error = errors[int((len(errors) - 1) * 0.8)] if len(errors) >= 3 else 0
    return hourly, min(4.5 + error, 8.0)


def plan(model: dict, now: datetime, current: float, base: float, maximum: float,
         green_maximum: float = 53.0) -> dict:
    hourly, margin = forecast(model, now)
    tomorrow, tomorrow_margin = forecast(model, now + timedelta(days=1))
    upcoming = [h for h in range(now.hour, 24) if hourly[h] >= 0.4]
    if upcoming:
        deadline = now.replace(hour=upcoming[0], minute=0, second=0, microsecond=0)
    else:
        hour = next((h for h, demand in enumerate(tomorrow) if demand >= 0.4), 7)
        deadline = (now + timedelta(days=1)).replace(hour=hour, minute=0, second=0, microsecond=0)
        margin = tomorrow_margin
    hours = max((deadline.timestamp() - now.timestamp()) / 3600, 0)
    remaining = sum(hourly[now.hour:]) if upcoming else sum(tomorrow)
    if upcoming:
        # Do not predict the same current-hour draw twice after it happened.
        observed_this_hour = number(model.get("today_draw", {}).get(str(now.hour)), 0)
        remaining -= min(observed_this_hour, hourly[now.hour])
    loss = max(number(model.get("standby_loss_c_per_h"), 0.5), 0)
    required = base + remaining + margin + min(loss * hours, 5)
    target = min(required, maximum)
    green_target = min(target, green_maximum)
    rate = max(number(model.get("green_c_per_h"), 2), 0.5)
    lead_hours = max((green_target - current) / rate, 0) * 1.25 + 0.5
    boost_rate = min(max(number(model.get("boost_c_per_h"), 4), 1), 12)
    boost_heating_hours = max((target - current) / boost_rate, 0) * 1.25 + 0.5
    recovery_lead_hours = boost_heating_hours + 0.75  # 45 minutes to answer.
    return {"target_c": round(target, 1), "required_uncapped_c": round(required, 1),
            "green_target_c": round(green_target, 1), "reserve_c": round(margin, 1),
            "expected_remaining_draw_c": round(remaining, 2),
            "deadline": deadline.isoformat(), "green_lead_hours": round(lead_hours, 2),
            "green_start": datetime.fromtimestamp(deadline.timestamp() - lead_hours * 3600, now.tzinfo).isoformat(),
            "green_due": hours <= lead_hours, "capacity_shortfall_c": round(max(required - maximum, 0), 1),
            "boost_c_per_h": boost_rate, "boost_heating_hours": round(boost_heating_hours, 2),
            "recovery_request_lead_hours": round(recovery_lead_hours, 2),
            "recovery_request_at": datetime.fromtimestamp(deadline.timestamp() - recovery_lead_hours * 3600, now.tzinfo).isoformat(),
            "recovery_request_due": hours <= recovery_lead_hours,
            "green_shortfall_c": round(max(required - green_maximum, 0), 1),
            "tomorrow_hourly_draw_c": [round(x, 2) for x in tomorrow],
            "tomorrow_reserve_c": round(tomorrow_margin, 1),
            "bootstrap": not bool(model.get("draw_by_day"))}

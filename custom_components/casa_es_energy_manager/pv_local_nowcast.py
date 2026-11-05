"""Validated short-term PV potential from measured local irradiance.

This estimates current available power, not tomorrow's energy. Training excludes
near-full batteries and high inverter output; acceptance uses separate days.
"""
from datetime import datetime, timedelta
from math import isfinite
from statistics import median, mean


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def epoch(value):
    result = number(value)
    return result / 1000 if result is not None and result > 1e12 else result


def coefficient(model, at):
    values = model.get("hours", {})
    hour = at.hour
    if str(hour) not in values:
        return None
    center = hour + at.minute / 60 + at.second / 3600 - .5
    left = int(center // 1)
    fraction = center - left
    a, b = values.get(str(left)), values.get(str(left + 1))
    if a and b:
        return a["coefficient"] * (1 - fraction) + b["coefficient"] * fraction
    return values[str(hour)]["coefficient"]


def fit(statistics, roles, now, nominal_w, layout_since=None):
    streams = {role: {epoch(r.get("start")): r for r in statistics.get(entity, [])}
               for role, entity in roles.items()}
    rows = []
    excluded_string_mismatch = excluded_charge_limit = 0
    for stamp, r in streams["irradiance"].items():
        if stamp is None:
            continue
        at = datetime.fromtimestamp(stamp, now.tzinfo)
        if at.date() >= now.date() or (layout_since and at.date().isoformat() <= layout_since):
            continue
        ghi = number(r.get("mean"))
        real = number(streams["actual"].get(stamp, {}).get("mean"))
        soc = number(streams["soc"].get(stamp, {}).get("mean"))
        forecast = number(streams["forecast"].get(stamp, {}).get("mean"))
        if None in (ghi, real, soc, forecast) or ghi < 100 or real < 500 or real >= nominal_w * .9 or soc >= 90:
            continue
        if "string1" in streams and "string2" in streams:
            p1 = number(streams["string1"].get(stamp, {}).get("mean"))
            p2 = number(streams["string2"].get(stamp, {}).get("mean"))
            if p1 is None or p2 is None or p1 < 0 or p2 < 0 or abs(p1 + p2 - real) > max(100, real * .05):
                excluded_string_mismatch += 1
                continue
        if "battery_power" in streams and "grid" in streams:
            charging = number(streams["battery_power"].get(stamp, {}).get("mean"))
            grid = number(streams["grid"].get(stamp, {}).get("mean"))
            # Positive battery power is charging for this verified inverter.
            # Near its 3.5 kW acceptance cap with no export, clipping cannot be
            # ruled out. Exclude conservatively rather than teach low PV yield.
            if charging is not None and grid is not None and charging >= 3300 and abs(grid) <= 100:
                excluded_charge_limit += 1
                continue
        rows.append((at, ghi, real, forecast))
    cutoff = now.date() - timedelta(days=2)
    train = [r for r in rows if r[0].date() < cutoff]
    validation = [r for r in rows if r[0].date() >= cutoff]
    hours = {}
    for hour in range(24):
        selected = [r for r in train if r[0].hour == hour]
        if len(selected) >= 12 and len({r[0].date() for r in selected}) >= 3:
            hours[str(hour)] = {"coefficient": median(r[2] / r[1] for r in selected),
                                "samples": len(selected),
                                "max_irradiance": max(r[1] for r in selected)}
    model = {"layout_since": layout_since, "hours": hours, "trained_at": now.isoformat(), "nominal_w": nominal_w,
             "excluded_string_mismatch": excluded_string_mismatch,
             "excluded_charge_limit": excluded_charge_limit, "training_samples": len(train), "training_days": len({r[0].date() for r in train})}
    paired = []
    for at, ghi, real, forecast in validation:
        c = coefficient(model, at)
        if c is not None:
            paired.append((abs(min(c * ghi, nominal_w) - real), abs(forecast - real)))
    if len(train) < 90 or len(paired) < 30:
        return {**model, "accepted": False, "reason": "insufficient_independent_samples"}
    local_error, baseline_error = mean(r[0] for r in paired), mean(r[1] for r in paired)
    accepted = baseline_error > 0 and local_error <= baseline_error * .9
    return {**model, "accepted": accepted, "validation_samples": len(paired),
            "validation_mae_w": round(local_error, 1), "weather_mae_w": round(baseline_error, 1),
            "reason": "validated_local_irradiance" if accepted else "no_validated_improvement"}


def estimate(model, irradiance, now, nominal_w, margin_pct=3, extra_max_w=200):
    ghi = number(irradiance)
    if ghi is None or ghi < 0 or not model.get("accepted"):
        return None
    trained = datetime.fromisoformat(model["trained_at"])
    if now.timestamp() - trained.timestamp() > 14 * 86400 or abs(model.get("nominal_w", 0) - nominal_w) > 1:
        return None
    if ghi <= 10:
        return 0.0
    c = coefficient(model, now)
    window = model.get("hours", {}).get(str(now.hour), {})
    if c is None or ghi > window.get("max_irradiance", 0) * 1.25:
        return None
    raw = max(c * ghi, 0)
    extra = min(raw * max(0, min(margin_pct, 10)) / 100, max(0, min(extra_max_w, 500)))
    return round(min(raw + extra, nominal_w), 1)


def combined(actual, hypothetical):
    values = [v for value in (actual, hypothetical) if (v := number(value)) is not None and v >= 0]
    return max(values) if values else None

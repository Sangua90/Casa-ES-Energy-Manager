"""Deterministic planning policy and guardrails for Casa ES."""

from __future__ import annotations

from datetime import datetime
from typing import Any

GRID_HEADROOM_PROTECT_W = 1000.0
PHASE_HEADROOM_PROTECT_W = 500.0
INVERTER_HEADROOM_PROTECT_W = 1000.0
GRID_HEADROOM_CRITICAL_W = 300.0
PHASE_HEADROOM_CRITICAL_W = 200.0
INVERTER_HEADROOM_CRITICAL_W = 500.0
SURPLUS_USEFUL_W = 400.0
SOLAR_ABSENT_W = 30.0
SOLAR_VERY_LOW_W = 200.0
SOLAR_LOW_W = 800.0
SOLAR_USEFUL_W = 2500.0
DEFINITE_SHORTFALL_MARGIN_KWH = 0.25
BATTERY_FIRST_MARGIN_KWH = 1.5
FLEXIBLE_ENERGY_SAFETY_BUFFER_KWH = 1.0


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integrate_forecast_curve_kwh(
    curve: list[dict[str, Any]],
    *,
    now: datetime,
    target: datetime,
) -> tuple[float | None, bool]:
    """Integrate a power forecast curve up to target using trapezoids."""
    points: list[tuple[datetime, float]] = []
    for item in curve:
        if not isinstance(item, dict):
            continue
        raw_time = item.get("time")
        power = _float(item.get("power_w"))
        if raw_time is None or power is None:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw_time))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            continue
        points.append((stamp, max(power, 0.0)))

    points.sort(key=lambda item: item[0])
    points = [item for item in points if item[0] >= now]
    if len(points) < 2:
        return None, False

    energy_wh = 0.0
    used_any = False
    for (time_a, power_a), (time_b, power_b) in zip(points, points[1:]):
        if time_a >= target:
            break
        end = min(time_b, target)
        if end <= time_a or time_b == time_a:
            continue
        duration_h = (end - time_a).total_seconds() / 3600.0
        if end < time_b:
            fraction = (end - time_a).total_seconds() / (time_b - time_a).total_seconds()
            end_power = power_a + (power_b - power_a) * fraction
        else:
            end_power = power_b
        energy_wh += (power_a + end_power) / 2.0 * duration_h
        used_any = True
        if end >= target:
            break

    if not used_any:
        return None, False

    complete = points[-1][0] >= target
    return round(energy_wh / 1000.0, 3), complete


def build_planner_policy(
    data: dict[str, Any],
    *,
    now: datetime,
    target: datetime,
    battery_capacity_kwh: float,
    battery_target_soc: float,
    expected_base_load_w: float = 500.0,
    battery_charge_efficiency_pct: float = 95.0,
) -> dict[str, Any]:
    """Build deterministic energy facts that constrain the advisory AI planner."""
    soc = _float(data.get("battery_soc")) or 0.0
    battery_energy_needed = (
        max(battery_target_soc - soc, 0.0) / 100.0 * battery_capacity_kwh
    )
    hours_to_target = max((target - now).total_seconds() / 3600.0, 0.0)

    base_load_w = max(float(expected_base_load_w), 0.0)
    base_load_energy = base_load_w * hours_to_target / 1000.0
    efficiency = max(0.5, min(float(battery_charge_efficiency_pct) / 100.0, 1.0))
    battery_input_energy_needed = battery_energy_needed / efficiency

    grid_headroom = _float(data.get("grid_headroom_w"))
    inverter_headroom = _float(data.get("inverter_headroom_w"))
    phase_headrooms = [
        value
        for value in (
            _float(data.get("phase_l1_headroom_w")),
            _float(data.get("phase_l2_headroom_w")),
            _float(data.get("phase_l3_headroom_w")),
        )
        if value is not None
    ]
    min_phase_headroom = min(phase_headrooms) if phase_headrooms else None

    warning = bool(
        data.get("grid_warning")
        or data.get("phase_warning")
        or data.get("inverter_warning")
    )
    protection_allowed = warning or bool(
        (grid_headroom is not None and grid_headroom <= GRID_HEADROOM_PROTECT_W)
        or (inverter_headroom is not None and inverter_headroom <= INVERTER_HEADROOM_PROTECT_W)
        or (min_phase_headroom is not None and min_phase_headroom <= PHASE_HEADROOM_PROTECT_W)
    )
    protection_required = warning or bool(
        (grid_headroom is not None and grid_headroom <= GRID_HEADROOM_CRITICAL_W)
        or (inverter_headroom is not None and inverter_headroom <= INVERTER_HEADROOM_CRITICAL_W)
        or (min_phase_headroom is not None and min_phase_headroom <= PHASE_HEADROOM_CRITICAL_W)
    )

    if protection_required:
        grid_pressure = "critical"
    elif protection_allowed:
        grid_pressure = "elevated"
    else:
        grid_pressure = "normal"

    measured = _float(data.get("pv_power_w")) or 0.0
    potential = _float(data.get("pv_potential_w")) or measured
    solar_reference = max(measured, potential)
    remaining_today = _float(data.get("forecast_remaining_kwh"))

    if solar_reference <= SOLAR_ABSENT_W and (remaining_today is None or remaining_today <= 0.02):
        solar_state = "absent"
    elif solar_reference <= SOLAR_VERY_LOW_W:
        solar_state = "very_low"
    elif solar_reference <= SOLAR_LOW_W:
        solar_state = "low"
    elif solar_reference <= SOLAR_USEFUL_W:
        solar_state = "useful"
    else:
        solar_state = "high"

    forecast_to_target, curve_complete = integrate_forecast_curve_kwh(
        data.get("forecast_curve") or [], now=now, target=target
    )

    margin_before_base = None
    margin_after_base = None
    flexible_budget = None
    if forecast_to_target is not None and curve_complete:
        margin_before_base = round(forecast_to_target - battery_input_energy_needed, 3)
        margin_after_base = round(
            forecast_to_target - battery_input_energy_needed - base_load_energy, 3
        )
        flexible_budget = round(
            max(margin_after_base - FLEXIBLE_ENERGY_SAFETY_BUFFER_KWH, 0.0), 3
        )

    definite_shortfall = bool(
        curve_complete
        and margin_after_base is not None
        and margin_after_base < -DEFINITE_SHORTFALL_MARGIN_KWH
    )
    if not curve_complete or margin_after_base is None:
        target_reachability = "unknown"
    elif definite_shortfall:
        target_reachability = "definite_shortfall"
    elif margin_after_base <= BATTERY_FIRST_MARGIN_KWH:
        target_reachability = "tight"
    else:
        target_reachability = "comfortable"

    grid_charge_allowed = bool(
        definite_shortfall and hours_to_target <= 6.0 and soc < battery_target_soc
    )
    battery_first_preferred = bool(
        soc < battery_target_soc
        and curve_complete
        and margin_after_base is not None
        and margin_after_base <= BATTERY_FIRST_MARGIN_KWH
    )

    potential_after_house = _float(data.get("pv_potential_after_house_w")) or 0.0
    surplus_allowed = bool(
        data.get("pv_curtailment_likely") or potential_after_house >= SURPLUS_USEFUL_W
    )

    if battery_first_preferred:
        fallback_strategy = "battery_first"
    elif surplus_allowed:
        fallback_strategy = "use_surplus"
    else:
        fallback_strategy = "balanced"

    return {
        "battery_energy_needed_kwh": round(battery_energy_needed, 3),
        "battery_input_energy_needed_kwh": round(battery_input_energy_needed, 3),
        "battery_charge_efficiency_pct": round(efficiency * 100.0, 1),
        "hours_to_target": round(hours_to_target, 2),
        "expected_base_load_w": round(base_load_w, 1),
        "base_load_energy_to_target_kwh": round(base_load_energy, 3),
        "forecast_energy_to_target_kwh": forecast_to_target,
        "forecast_curve_complete_to_target": curve_complete,
        "forecast_margin_before_base_load_kwh": margin_before_base,
        "forecast_margin_after_base_load_kwh": margin_after_base,
        "flexible_energy_budget_kwh": flexible_budget,
        "flexible_energy_safety_buffer_kwh": FLEXIBLE_ENERGY_SAFETY_BUFFER_KWH,
        "target_reachability": target_reachability,
        "grid_pressure": grid_pressure,
        "protect_grid_allowed": protection_allowed,
        "protect_grid_required": protection_required,
        "grid_charge_allowed": grid_charge_allowed,
        "battery_first_preferred": battery_first_preferred,
        "use_surplus_allowed": surplus_allowed,
        "solar_state": solar_state,
        "min_phase_headroom_w": min_phase_headroom,
        "fallback_strategy": fallback_strategy,
    }


def apply_ai_guardrails(
    generated: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Apply deterministic safety/consistency constraints to an AI result."""
    raw_strategy = str(generated.get("strategy", "insufficient_data")).strip()
    strategy = raw_strategy
    guardrail_reason: str | None = None

    if policy.get("protect_grid_required"):
        if strategy != "protect_grid":
            guardrail_reason = "Protezione elettrica richiesta dai margini locali."
        strategy = "protect_grid"
    elif strategy == "protect_grid" and not policy.get("protect_grid_allowed"):
        strategy = str(policy.get("fallback_strategy") or "balanced")
        guardrail_reason = "protect_grid rifiutata: margini rete/fasi/inverter normali."
    elif strategy == "grid_charge" and not policy.get("grid_charge_allowed"):
        strategy = str(policy.get("fallback_strategy") or "balanced")
        guardrail_reason = "grid_charge rifiutata: carenza energetica al target non dimostrata."

    allow_flexible = bool(generated.get("allow_flexible_loads", False))
    if policy.get("protect_grid_required") or policy.get("target_reachability") == "definite_shortfall":
        allow_flexible = False

    grid_charge_recommended = bool(generated.get("grid_charge_recommended", False))
    if not policy.get("grid_charge_allowed"):
        grid_charge_recommended = False

    return {
        "strategy": strategy,
        "raw_strategy": raw_strategy,
        "allow_flexible_loads": allow_flexible,
        "grid_charge_recommended": grid_charge_recommended,
        "guardrail_applied": guardrail_reason is not None,
        "guardrail_reason": guardrail_reason,
    }

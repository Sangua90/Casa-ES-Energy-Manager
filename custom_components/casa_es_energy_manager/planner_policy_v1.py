"""v1 policy wrapper adding the user's global energy preference."""

from __future__ import annotations

from typing import Any

from .const import (
    ENERGY_PREFERENCE_BATTERY,
    ENERGY_PREFERENCE_BALANCED,
    ENERGY_PREFERENCE_LOADS,
)
from .planner_policy import (
    apply_ai_guardrails,
    build_planner_policy as _base_build_planner_policy,
    integrate_forecast_curve_kwh,
)

PREFERENCE_BUFFER_KWH = {
    ENERGY_PREFERENCE_BATTERY: 2.0,
    ENERGY_PREFERENCE_BALANCED: 1.0,
    ENERGY_PREFERENCE_LOADS: 0.5,
}
PREFERENCE_BATTERY_FIRST_MARGIN_KWH = {
    ENERGY_PREFERENCE_BATTERY: 3.0,
    ENERGY_PREFERENCE_BALANCED: 1.5,
    ENERGY_PREFERENCE_LOADS: 0.75,
}


def build_planner_policy(
    data: dict[str, Any],
    *,
    now: Any,
    target: Any,
    battery_capacity_kwh: float,
    battery_target_soc: float,
    expected_base_load_w: float = 500.0,
    battery_charge_efficiency_pct: float = 95.0,
    energy_preference: str = ENERGY_PREFERENCE_BALANCED,
) -> dict[str, Any]:
    """Build base deterministic policy and apply the chosen energy preference.

    Preference only changes how conservative the flexible-energy reservation is.
    It never weakens measured electrical protection or definite-shortfall rules.
    """
    if energy_preference not in PREFERENCE_BUFFER_KWH:
        energy_preference = ENERGY_PREFERENCE_BALANCED

    policy = _base_build_planner_policy(
        data,
        now=now,
        target=target,
        battery_capacity_kwh=battery_capacity_kwh,
        battery_target_soc=battery_target_soc,
        expected_base_load_w=expected_base_load_w,
        battery_charge_efficiency_pct=battery_charge_efficiency_pct,
    )

    margin = policy.get("forecast_margin_after_base_load_kwh")
    curve_complete = bool(policy.get("forecast_curve_complete_to_target"))
    buffer_kwh = PREFERENCE_BUFFER_KWH[energy_preference]
    battery_first_margin = PREFERENCE_BATTERY_FIRST_MARGIN_KWH[energy_preference]

    if margin is not None and curve_complete:
        policy["flexible_energy_budget_kwh"] = round(max(float(margin) - buffer_kwh, 0.0), 3)

    try:
        soc = float(data.get("battery_soc") or 0.0)
    except (TypeError, ValueError):
        soc = 0.0
    policy["battery_first_preferred"] = bool(
        soc < battery_target_soc
        and curve_complete
        and margin is not None
        and float(margin) <= battery_first_margin
        and policy.get("target_reachability") != "definite_shortfall"
    )

    if policy["battery_first_preferred"]:
        policy["fallback_strategy"] = "battery_first"
    elif policy.get("use_surplus_allowed"):
        policy["fallback_strategy"] = "use_surplus"
    else:
        policy["fallback_strategy"] = "balanced"

    policy["energy_preference"] = energy_preference
    policy["flexible_energy_safety_buffer_kwh"] = buffer_kwh
    policy["battery_first_margin_kwh"] = battery_first_margin
    return policy


__all__ = ["apply_ai_guardrails", "build_planner_policy", "integrate_forecast_curve_kwh"]

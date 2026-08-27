"""Pure calculations used by Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any


def calculate_metrics(
    *,
    pv_power_w: float,
    load_power_w: float,
    grid_power_w: float,
    battery_power_w: float,
    phase_l1_w: float | None,
    phase_l2_w: float | None,
    phase_l3_w: float | None,
    inverter_limit_w: float,
    phase_limit_w: float,
    grid_limit_w: float,
    safety_margin_w: float,
    inverter_safety_margin_w: float | None = None,
    phase_safety_margin_w: float | None = None,
    grid_safety_margin_w: float | None = None,
    pv_potential_power_w: float | None = None,
    battery_soc: float | None = None,
    curtailment_soc_threshold: float = 98.0,
    curtailment_potential_gap_w: float = 400.0,
    curtailment_grid_import_max_w: float = 150.0,
) -> dict[str, Any]:
    """Calculate read-only energy, solar-opportunity and protection metrics.

    ``safety_margin_w`` is retained as a backward-compatible fallback. v1.5.1
    allows separate margins for inverter, each phase and grid contract.
    """
    grid_import_w = max(grid_power_w, 0.0)
    grid_export_w = max(-grid_power_w, 0.0)
    battery_charge_w = max(battery_power_w, 0.0)
    battery_discharge_w = max(-battery_power_w, 0.0)

    solar_after_house_w = max(pv_power_w - load_power_w, 0.0)
    potential_input = (
        pv_power_w if pv_potential_power_w is None else max(pv_potential_power_w, 0.0)
    )
    pv_potential_w = max(pv_power_w, potential_input)
    pv_potential_gap_w = max(pv_potential_w - pv_power_w, 0.0)
    pv_potential_after_house_w = max(pv_potential_w - load_power_w, 0.0)

    pv_curtailment_likely = bool(
        battery_soc is not None
        and battery_soc >= curtailment_soc_threshold
        and grid_import_w <= curtailment_grid_import_max_w
        and pv_potential_gap_w >= curtailment_potential_gap_w
    )

    inverter_margin = (
        safety_margin_w if inverter_safety_margin_w is None else inverter_safety_margin_w
    )
    phase_margin = safety_margin_w if phase_safety_margin_w is None else phase_safety_margin_w
    grid_margin = safety_margin_w if grid_safety_margin_w is None else grid_safety_margin_w

    safe_grid_limit_w = max(grid_limit_w - grid_margin, 0.0)
    safe_phase_limit_w = max(phase_limit_w - phase_margin, 0.0)
    safe_inverter_limit_w = max(inverter_limit_w - inverter_margin, 0.0)

    grid_headroom_w = max(safe_grid_limit_w - grid_import_w, 0.0)
    inverter_headroom_w = max(safe_inverter_limit_w - load_power_w, 0.0)

    phases = {"l1": phase_l1_w, "l2": phase_l2_w, "l3": phase_l3_w}
    phase_headroom: dict[str, float | None] = {}
    phase_warning = False
    hottest_phase: str | None = None
    hottest_phase_power = -1.0

    for phase, power in phases.items():
        if power is None:
            phase_headroom[phase] = None
            continue
        positive_power = max(power, 0.0)
        phase_headroom[phase] = max(safe_phase_limit_w - positive_power, 0.0)
        if positive_power >= safe_phase_limit_w:
            phase_warning = True
        if positive_power > hottest_phase_power:
            hottest_phase = phase
            hottest_phase_power = positive_power

    grid_warning = grid_import_w >= safe_grid_limit_w
    inverter_warning = load_power_w >= safe_inverter_limit_w

    if grid_warning:
        status = "grid_warning"
    elif phase_warning:
        status = "phase_warning"
    elif inverter_warning:
        status = "inverter_warning"
    elif pv_curtailment_likely:
        status = "pv_curtailment_likely"
    else:
        status = "monitoring"

    return {
        "grid_import_w": grid_import_w,
        "grid_export_w": grid_export_w,
        "battery_charge_w": battery_charge_w,
        "battery_discharge_w": battery_discharge_w,
        "solar_after_house_w": solar_after_house_w,
        "pv_potential_w": pv_potential_w,
        "pv_potential_gap_w": pv_potential_gap_w,
        "pv_potential_after_house_w": pv_potential_after_house_w,
        "pv_curtailment_likely": pv_curtailment_likely,
        "grid_headroom_w": grid_headroom_w,
        "inverter_headroom_w": inverter_headroom_w,
        "phase_l1_headroom_w": phase_headroom["l1"],
        "phase_l2_headroom_w": phase_headroom["l2"],
        "phase_l3_headroom_w": phase_headroom["l3"],
        "grid_warning": grid_warning,
        "phase_warning": phase_warning,
        "inverter_warning": inverter_warning,
        "hottest_phase": hottest_phase,
        "status": status,
        "inverter_safety_margin_w": float(inverter_margin),
        "phase_safety_margin_w": float(phase_margin),
        "grid_safety_margin_w": float(grid_margin),
    }

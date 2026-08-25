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
) -> dict[str, Any]:
    """Calculate read-only energy and protection metrics.

    Sign conventions are intentionally fixed for the Casa ES installation:
    * grid_power_w > 0 means grid import
    * grid_power_w < 0 means grid export
    * battery_power_w > 0 means battery charging
    * battery_power_w < 0 means battery discharging
    """
    grid_import_w = max(grid_power_w, 0.0)
    grid_export_w = max(-grid_power_w, 0.0)
    battery_charge_w = max(battery_power_w, 0.0)
    battery_discharge_w = max(-battery_power_w, 0.0)

    # PV currently produced after the measured house load. On Casa ES this
    # power is commonly being directed to the battery. It is NOT a claim
    # about curtailed PV potential; that will be modelled in a later release.
    solar_after_house_w = max(pv_power_w - load_power_w, 0.0)

    safe_grid_limit_w = max(grid_limit_w - safety_margin_w, 0.0)
    safe_phase_limit_w = max(phase_limit_w - safety_margin_w, 0.0)
    safe_inverter_limit_w = max(inverter_limit_w - safety_margin_w, 0.0)

    grid_headroom_w = max(safe_grid_limit_w - grid_import_w, 0.0)
    inverter_headroom_w = max(safe_inverter_limit_w - load_power_w, 0.0)

    phases = {
        "l1": phase_l1_w,
        "l2": phase_l2_w,
        "l3": phase_l3_w,
    }
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
    else:
        status = "monitoring"

    return {
        "grid_import_w": grid_import_w,
        "grid_export_w": grid_export_w,
        "battery_charge_w": battery_charge_w,
        "battery_discharge_w": battery_discharge_w,
        "solar_after_house_w": solar_after_house_w,
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
    }

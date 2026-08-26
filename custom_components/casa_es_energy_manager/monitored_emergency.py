"""Pure helpers for v1.4 monitored-load emergency shedding."""

from __future__ import annotations

from typing import Any

from .const import (
    CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
    CONF_MONITORED_LOAD_ENABLED,
    CONF_MONITORED_LOAD_PHASE,
    MONITORED_EMERGENCY_ACTIVE_POWER_THRESHOLD_W,
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def monitored_load_is_drawing(load: dict[str, Any]) -> bool:
    """Return True only for an enabled, available load drawing real power."""
    return bool(
        load.get(CONF_MONITORED_LOAD_ENABLED, True)
        and load.get("available", False)
        and load.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY)
        and _number(load.get("current_power_w"))
        > MONITORED_EMERGENCY_ACTIVE_POWER_THRESHOLD_W
    )


def warning_phases(data: dict[str, Any]) -> set[str]:
    """Return phases that are at/over their safe measured limit."""
    if not data.get("phase_warning"):
        return set()

    phases: set[str] = set()
    for phase in ("l1", "l2", "l3"):
        headroom = data.get(f"phase_{phase}_headroom_w")
        if headroom is not None and _number(headroom) <= 1e-9:
            phases.add(phase)

    # Older/synthetic data may only expose the aggregate warning. The hottest
    # phase is the safest fallback when available; otherwise the caller can
    # conservatively treat the warning as not phase-identifiable.
    if not phases:
        hottest = str(data.get("hottest_phase") or "")
        if hottest in {"l1", "l2", "l3"}:
            phases.add(hottest)
    return phases


def eligible_emergency_loads(
    loads: list[dict[str, Any]],
    *,
    phases: set[str] | None = None,
    excluded_subentry_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return active monitored loads that can really reduce the relevant limit."""
    excluded = excluded_subentry_ids or set()
    result: list[dict[str, Any]] = []
    for load in loads:
        subentry_id = str(load.get("subentry_id") or "")
        if subentry_id and subentry_id in excluded:
            continue
        if not monitored_load_is_drawing(load):
            continue
        phase = str(load.get(CONF_MONITORED_LOAD_PHASE) or "")
        if phases is not None and phase not in phases and phase != "three_phase":
            continue
        result.append(load)
    return result


def choose_relief_candidate(
    loads: list[dict[str, Any]], required_relief_w: float
) -> dict[str, Any] | None:
    """Choose the least disruptive single load likely to solve the overload.

    If one monitored load is large enough by itself, choose the smallest such
    load. Otherwise choose the largest available load and re-measure on the next
    coordinator refresh before shedding anything else.
    """
    if not loads:
        return None
    required = max(_number(required_relief_w), 0.0)
    ordered = sorted(loads, key=lambda item: _number(item.get("current_power_w")))
    sufficient = [
        item for item in ordered if _number(item.get("current_power_w")) >= required
    ]
    if sufficient:
        return sufficient[0]
    return ordered[-1]


def grid_relief_w(data: dict[str, Any], safe_grid_limit_w: float) -> float:
    return max(_number(data.get("grid_import_w")) - max(safe_grid_limit_w, 0.0), 0.0)


def inverter_relief_w(data: dict[str, Any], safe_inverter_limit_w: float) -> float:
    return max(_number(data.get("load_power_w")) - max(safe_inverter_limit_w, 0.0), 0.0)


def phase_relief_w(
    data: dict[str, Any], phase: str, safe_phase_limit_w: float
) -> float:
    return max(
        _number(data.get(f"phase_{phase}_power_w")) - max(safe_phase_limit_w, 0.0),
        0.0,
    )


def most_overloaded_phase(
    data: dict[str, Any], phases: set[str], safe_phase_limit_w: float
) -> tuple[str | None, float]:
    """Return the warned phase with the largest measured excess."""
    if not phases:
        return None, 0.0
    ranked = sorted(
        (
            (phase, phase_relief_w(data, phase, safe_phase_limit_w))
            for phase in phases
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[0]

"""Pure phase-attribution math for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any


def phase_attribution(
    monitored: list[dict[str, Any]],
    managed: list[dict[str, Any]],
    *,
    phase_l1_w: float | None,
    phase_l2_w: float | None,
    phase_l3_w: float | None,
) -> dict[str, Any]:
    """Attribute measured phase load without double-counting phase totals.

    Monitored and managed-device power sensors explain parts of the already measured
    phase totals. They are never added to the electrical safety input.
    """
    known = {"l1": 0.0, "l2": 0.0, "l3": 0.0}
    items: list[dict[str, Any]] = []

    for source, kind in ((monitored, "monitorato"), (managed, "gestito")):
        for item in source:
            if not bool(item.get("enabled", True)):
                continue
            try:
                power_w = max(float(item.get("current_power_w") or 0.0), 0.0)
            except (TypeError, ValueError):
                power_w = 0.0
            phase = str(item.get("phase") or "unknown")
            if phase == "three_phase":
                share = power_w / 3.0
                for key in known:
                    known[key] += share
            elif phase in known:
                known[phase] += power_w
            items.append(
                {
                    "name": item.get("name"),
                    "type": kind,
                    "phase": phase,
                    "power_w": round(power_w, 1),
                    "available": item.get("available"),
                }
            )

    totals = {"l1": phase_l1_w, "l2": phase_l2_w, "l3": phase_l3_w}
    other: dict[str, float | None] = {}
    for phase, total in totals.items():
        if total is None:
            other[phase] = None
        else:
            other[phase] = round(max(float(total) - known[phase], 0.0), 1)

    return {
        "monitored_load_count": len(monitored),
        "monitored_loads": monitored,
        "phase_known_load_l1_w": round(known["l1"], 1),
        "phase_known_load_l2_w": round(known["l2"], 1),
        "phase_known_load_l3_w": round(known["l3"], 1),
        "phase_other_load_l1_w": other["l1"],
        "phase_other_load_l2_w": other["l2"],
        "phase_other_load_l3_w": other["l3"],
        "phase_load_breakdown": items,
    }

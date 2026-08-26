"""Pure phase-attribution math for Casa ES Energy Manager."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

OFF_STATES = {"", "off", "unknown", "unavailable", "none"}


def _power(item: dict[str, Any]) -> float:
    try:
        return max(float(item.get("current_power_w") or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _entity_active(item: dict[str, Any]) -> bool:
    return str(item.get("state") or "").strip().lower() not in OFF_STATES


def phase_attribution(
    monitored: list[dict[str, Any]],
    managed: list[dict[str, Any]],
    *,
    phase_l1_w: float | None,
    phase_l2_w: float | None,
    phase_l3_w: float | None,
) -> dict[str, Any]:
    """Attribute measured phase load without double-counting phase totals.

    Individual meters only explain parts of the already measured L1/L2/L3 totals.
    A shared meter is counted once. If only one child entity is logically active,
    that child owns the measured watts. If multiple active children are on
    different phases, the shared watts remain unattributed rather than guessed.
    """
    known = {"l1": 0.0, "l2": 0.0, "l3": 0.0}
    items: list[dict[str, Any]] = []

    def add_known(phase: str, power_w: float) -> None:
        if phase == "three_phase":
            share = power_w / 3.0
            for key in known:
                known[key] += share
        elif phase in known:
            known[phase] += power_w

    def append_item(
        item: dict[str, Any],
        *,
        kind: str,
        power_w: float,
        shared_meter: bool = False,
        attribution: str | None = None,
    ) -> None:
        phase = str(item.get("phase") or "unknown")
        add_known(phase, power_w)
        result = {
            "name": item.get("name"),
            "type": kind,
            "phase": phase,
            "power_w": round(power_w, 1),
            "available": item.get("available"),
        }
        if shared_meter:
            result["shared_meter"] = True
        if attribution:
            result["attribution"] = attribution
        items.append(result)

    # Monitored loads are read-only and normally have dedicated meters.
    seen_monitored_sensors: set[str] = set()
    for item in monitored:
        if not bool(item.get("enabled", True)):
            continue
        sensor = str(item.get("power_sensor") or "")
        power_w = _power(item)
        if sensor and sensor in seen_monitored_sensors:
            power_w = 0.0
        elif sensor:
            seen_monitored_sensors.add(sensor)
        append_item(item, kind="monitorato", power_w=power_w)

    shared_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in managed:
        if not bool(item.get("enabled", True)):
            continue
        sensor = str(item.get("power_sensor") or "")
        if item.get("adaptive_shared_power_sensor") and sensor:
            shared_groups[sensor].append(item)
        else:
            append_item(item, kind="gestito", power_w=_power(item))

    for sensor, group in shared_groups.items():
        active = [item for item in group if _entity_active(item)]
        measured = max((_power(item) for item in group), default=0.0)

        if not active:
            for item in group:
                append_item(
                    item,
                    kind="gestito",
                    power_w=0.0,
                    shared_meter=True,
                    attribution="inactive_shared_meter",
                )
            continue

        active_phases = {str(item.get("phase") or "unknown") for item in active}
        if len(active_phases) == 1:
            owner = active[0]
            for item in group:
                is_owner = item is owner
                append_item(
                    item,
                    kind="gestito",
                    power_w=measured if is_owner else 0.0,
                    shared_meter=True,
                    attribution="shared_meter_owner" if is_owner else "shared_meter_sibling",
                )
            continue

        # Different active phases behind one aggregate meter cannot be split
        # safely. Leave the watts in `other load` rather than inventing a phase.
        for item in group:
            append_item(
                item,
                kind="gestito",
                power_w=0.0,
                shared_meter=True,
                attribution="ambiguous_shared_meter",
            )
        items.append(
            {
                "name": f"Meter condiviso {sensor}",
                "type": "meter_condiviso",
                "phase": "ambiguous",
                "power_w": round(measured, 1),
                "available": all(item.get("available") is not False for item in group),
                "shared_meter": True,
                "attribution": "not_added_to_known_phase",
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

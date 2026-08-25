"""Deterministic dry-run admission for managed Casa ES loads.

This module never calls Home Assistant services. It only evaluates whether a
configured flexible load could be admitted now, while respecting instantaneous
electrical limits and the energy budget calculated by the local planner policy.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

LEGACY_PRIORITY_MAP = {
    "very_high": 1,
    "high": 3,
    "normal": 5,
    "low": 7,
    "very_low": 10,
}
DEFAULT_NUMERIC_PRIORITY = 5
OFF_STATES = {"off", "unavailable", "unknown", "none", ""}
RUNNING_POWER_THRESHOLD_W = 20.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _priority_number(value: Any) -> int:
    """Return priority 1..10, accepting legacy text values from v0.4.0."""
    if isinstance(value, str) and value in LEGACY_PRIORITY_MAP:
        return LEGACY_PRIORITY_MAP[value]
    try:
        priority = int(round(float(value)))
    except (TypeError, ValueError):
        priority = DEFAULT_NUMERIC_PRIORITY
    return max(1, min(10, priority))


def _is_running(state: Any, current_power_w: Any = None) -> bool:
    """Return whether a managed load is currently active.

    A configured real-power sensor wins over generic entity state. This avoids
    treating a climate/water-heater mode as active consumption when the compressor
    or heater is actually idle.
    """
    if current_power_w is not None:
        return _number(current_power_w) >= RUNNING_POWER_THRESHOLD_W
    return str(state or "").strip().lower() not in OFF_STATES


def _phase_requirements(phase: str, nominal_power_w: float) -> dict[str, float]:
    """Return additional per-phase power required by a prospective start."""
    if phase == "three_phase":
        share = nominal_power_w / 3.0
        return {"l1": share, "l2": share, "l3": share}
    if phase in {"l1", "l2", "l3"}:
        return {phase: nominal_power_w}
    return {}


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


def _inside_time_window(now: datetime | None, start_value: Any, end_value: Any) -> bool:
    """Return whether now is inside an optional daily time window."""
    if now is None:
        return True
    start = _parse_time(start_value)
    end = _parse_time(end_value)
    if start is None and end is None:
        return True

    current = now.timetz().replace(tzinfo=None)
    if start is not None and end is None:
        return current >= start
    if start is None and end is not None:
        return current <= end
    assert start is not None and end is not None
    if start <= end:
        return start <= current <= end
    # Window crosses midnight, e.g. 22:00 -> 06:00.
    return current >= start or current <= end


def evaluate_managed_devices(
    devices: list[dict[str, Any]],
    *,
    data: dict[str, Any],
    policy: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Allocate current power and future energy to configured loads in priority order.

    Priority is numeric: 1 is the highest priority and 10 the lowest.
    The future energy budget only says whether a load can fit before the battery
    target. A load is considered admissible *now* only when sufficient current
    PV opportunity exists, unless that device explicitly allows grid energy.
    """
    flexible_budget = policy.get("flexible_energy_budget_kwh")
    energy_budget_known = flexible_budget is not None
    remaining_energy_kwh = max(_number(flexible_budget), 0.0)

    phase_headroom = {
        "l1": max(_number(data.get("phase_l1_headroom_w")), 0.0),
        "l2": max(_number(data.get("phase_l2_headroom_w")), 0.0),
        "l3": max(_number(data.get("phase_l3_headroom_w")), 0.0),
    }
    grid_headroom_w = max(_number(data.get("grid_headroom_w")), 0.0)
    inverter_headroom_w = max(_number(data.get("inverter_headroom_w")), 0.0)
    solar_opportunity_w = max(
        _number(data.get("solar_after_house_w")),
        _number(data.get("pv_potential_after_house_w")),
        0.0,
    )
    remaining_solar_w = solar_opportunity_w

    soc = _number(data.get("battery_soc"))
    protection_required = bool(policy.get("protect_grid_required"))
    target_state = str(policy.get("target_reachability") or "unknown")
    battery_first = bool(policy.get("battery_first_preferred"))

    normalized: list[dict[str, Any]] = []
    for device in devices:
        nominal = max(_number(device.get("nominal_power_w")), 0.0)
        runtime_minutes = max(
            _number(device.get("expected_runtime_minutes"), 60.0), 1.0
        )
        expected_energy = nominal * runtime_minutes / 60_000.0
        current_power = device.get("current_power_w")
        normalized.append(
            {
                **device,
                "nominal_power_w": nominal,
                "expected_runtime_minutes": runtime_minutes,
                "expected_energy_kwh": round(expected_energy, 3),
                "priority": _priority_number(device.get("priority")),
                "phase": str(device.get("phase") or "unknown"),
                "allow_grid": bool(device.get("allow_grid", False)),
                "max_grid_power_w": max(_number(device.get("max_grid_power_w")), 0.0),
                "enabled": bool(device.get("enabled", True)),
                "min_battery_soc": _number(device.get("min_battery_soc"), 0.0),
                "switch_interval_seconds": max(
                    _number(device.get("switch_interval_seconds"), 0.0), 0.0
                ),
                "running": _is_running(device.get("state"), current_power),
            }
        )

    running_by_entity = {
        str(item.get("entity_id")): item["running"]
        for item in normalized
        if item.get("entity_id")
    }

    running_commitment = sum(
        item["expected_energy_kwh"]
        for item in normalized
        if item["enabled"] and item["running"]
    )
    if energy_budget_known:
        remaining_energy_kwh = max(remaining_energy_kwh - running_commitment, 0.0)

    candidates = sorted(
        normalized,
        key=lambda item: (
            item["priority"],
            str(item.get("name") or item.get("entity_id") or ""),
        ),
    )

    decisions: list[dict[str, Any]] = []
    for item in candidates:
        decision = "blocked"
        reason = "Configurazione non valida."
        would_start = False
        solar_used_w = 0.0
        grid_needed_w = 0.0

        requires_entity = str(item.get("requires_entity") or "")
        if requires_entity:
            dependency_running = running_by_entity.get(
                requires_entity,
                _is_running(item.get("requires_state")),
            )
        else:
            dependency_running = True

        seconds_since_change = item.get("seconds_since_change")
        interval_ok = (
            seconds_since_change is None
            or _number(seconds_since_change) + 1e-9 >= item["switch_interval_seconds"]
        )

        if not item["enabled"]:
            decision = "disabled"
            reason = "Dispositivo disabilitato nella gestione Casa ES."
        elif item.get("available") is False:
            decision = "blocked"
            reason = "Entità dispositivo non disponibile."
        elif item["running"]:
            decision = "already_running"
            reason = "Dispositivo già attivo; nessun nuovo avvio simulato."
        elif item["nominal_power_w"] <= 0:
            decision = "blocked"
            reason = "Potenza nominale non valida."
        elif not _inside_time_window(
            now, item.get("start_after"), item.get("end_before")
        ):
            decision = "waiting_time"
            reason = "Fuori dalla finestra oraria consentita per questo dispositivo."
        elif not dependency_running:
            decision = "waiting_dependency"
            reason = "Il dispositivo richiesto come dipendenza non è attivo."
        elif not interval_ok:
            decision = "waiting_interval"
            reason = "Intervallo minimo tra commutazioni non ancora trascorso."
        elif protection_required:
            decision = "blocked"
            reason = "Protezione elettrica locale attiva."
        elif soc < item["min_battery_soc"]:
            decision = "blocked"
            reason = "SOC batteria sotto il minimo del dispositivo."
        elif target_state == "definite_shortfall":
            decision = "blocked"
            reason = "Carenza energetica prevista prima del target batteria."
        elif battery_first:
            decision = "waiting_energy"
            reason = "Margine batteria stretto: priorità temporanea alla ricarica."
        elif not energy_budget_known:
            decision = "waiting_energy"
            reason = "Budget energetico futuro non disponibile."
        elif item["expected_energy_kwh"] > remaining_energy_kwh + 1e-9:
            decision = "waiting_energy"
            reason = "Budget energetico flessibile insufficiente."
        elif item["nominal_power_w"] > inverter_headroom_w + 1e-9:
            decision = "blocked"
            reason = "Margine inverter insufficiente per un nuovo avvio."
        else:
            phase_need = _phase_requirements(item["phase"], item["nominal_power_w"])
            if not phase_need:
                decision = "blocked"
                reason = "Fase elettrica non configurata."
            elif any(
                required > phase_headroom.get(phase, 0.0) + 1e-9
                for phase, required in phase_need.items()
            ):
                decision = "blocked"
                reason = "Margine insufficiente sulla fase assegnata."
            elif item["allow_grid"]:
                solar_used_w = min(remaining_solar_w, item["nominal_power_w"])
                grid_needed_w = max(item["nominal_power_w"] - solar_used_w, 0.0)
                max_grid = item["max_grid_power_w"]
                if grid_needed_w > grid_headroom_w + 1e-9:
                    decision = "blocked"
                    reason = "Margine rete insufficiente per l'integrazione richiesta."
                elif max_grid > 0 and grid_needed_w > max_grid + 1e-9:
                    decision = "waiting_solar"
                    reason = "Serve più FV: l'integrazione rete richiesta supera il limite del dispositivo."
                else:
                    decision = "admissible_now"
                    reason = "Potenza, fase e budget disponibili; integrazione rete entro il limite configurato."
                    would_start = True
            elif item["nominal_power_w"] > remaining_solar_w + 1e-9:
                decision = "waiting_solar"
                reason = "Budget futuro disponibile, ma potenza FV disponibile ora insufficiente."
            else:
                solar_used_w = item["nominal_power_w"]
                decision = "admissible_now"
                reason = "Potenza FV, fase e budget energetico disponibili."
                would_start = True

        if would_start:
            remaining_energy_kwh = max(
                remaining_energy_kwh - item["expected_energy_kwh"], 0.0
            )
            inverter_headroom_w = max(
                inverter_headroom_w - item["nominal_power_w"], 0.0
            )
            for phase, required in _phase_requirements(
                item["phase"], item["nominal_power_w"]
            ).items():
                phase_headroom[phase] = max(phase_headroom[phase] - required, 0.0)
            remaining_solar_w = max(remaining_solar_w - solar_used_w, 0.0)
            grid_headroom_w = max(grid_headroom_w - grid_needed_w, 0.0)

        decisions.append(
            {
                "subentry_id": item.get("subentry_id"),
                "name": item.get("name"),
                "entity_id": item.get("entity_id"),
                "state": item.get("state"),
                "running": item["running"],
                "priority": item["priority"],
                "phase": item["phase"],
                "nominal_power_w": round(item["nominal_power_w"], 1),
                "current_power_w": item.get("current_power_w"),
                "expected_runtime_minutes": round(
                    item["expected_runtime_minutes"], 1
                ),
                "expected_energy_kwh": item["expected_energy_kwh"],
                "allow_grid": item["allow_grid"],
                "max_grid_power_w": round(item["max_grid_power_w"], 1),
                "estimated_grid_needed_w": round(grid_needed_w, 1),
                "min_battery_soc": item["min_battery_soc"],
                "requires_entity": item.get("requires_entity"),
                "start_after": item.get("start_after"),
                "end_before": item.get("end_before"),
                "switch_interval_seconds": round(item["switch_interval_seconds"], 1),
                "decision": decision,
                "reason": reason,
                "would_start": would_start,
            }
        )

    admissible = sum(1 for item in decisions if item["would_start"])
    running = sum(1 for item in decisions if item["running"])
    waiting = sum(
        1
        for item in decisions
        if item["decision"]
        in {
            "waiting_solar",
            "waiting_energy",
            "waiting_time",
            "waiting_dependency",
            "waiting_interval",
        }
    )

    if not decisions:
        status = "no_devices"
    elif protection_required:
        status = "protected"
    elif admissible:
        status = "devices_available"
    elif waiting:
        status = "waiting"
    else:
        status = "blocked"

    return {
        "dry_run_status": status,
        "managed_device_count": len(decisions),
        "managed_devices_running": running,
        "managed_devices_admissible_now": admissible,
        "managed_devices_waiting": waiting,
        "dry_run_solar_opportunity_w": round(solar_opportunity_w, 1),
        "dry_run_running_energy_commitment_kwh": round(running_commitment, 3),
        "dry_run_remaining_flexible_budget_kwh": (
            round(remaining_energy_kwh, 3) if energy_budget_known else None
        ),
        "dry_run_decisions": decisions,
    }

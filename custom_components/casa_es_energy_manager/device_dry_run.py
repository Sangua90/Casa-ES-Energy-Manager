"""Deterministic dry-run admission for managed Casa ES loads.

This module never calls Home Assistant services. It only evaluates whether a
configured flexible load could be admitted now, while respecting instantaneous
electrical limits and the energy budget calculated by the local planner policy.
"""

from __future__ import annotations

from typing import Any

PRIORITY_RANK = {
    "very_high": 0,
    "high": 1,
    "normal": 2,
    "low": 3,
    "very_low": 4,
}

OFF_STATES = {"off", "unavailable", "unknown", "none", ""}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_running(state: Any) -> bool:
    """Return a conservative generic running state for supported load entities."""
    return str(state or "").strip().lower() not in OFF_STATES


def _phase_requirements(phase: str, nominal_power_w: float) -> dict[str, float]:
    """Return additional per-phase power required by a prospective start."""
    if phase == "three_phase":
        share = nominal_power_w / 3.0
        return {"l1": share, "l2": share, "l3": share}
    if phase in {"l1", "l2", "l3"}:
        return {phase: nominal_power_w}
    return {}


def evaluate_managed_devices(
    devices: list[dict[str, Any]],
    *,
    data: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Allocate current power and future energy to configured loads in priority order.

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
        runtime_minutes = max(_number(device.get("expected_runtime_minutes"), 60.0), 1.0)
        expected_energy = nominal * runtime_minutes / 60_000.0
        normalized.append(
            {
                **device,
                "nominal_power_w": nominal,
                "expected_runtime_minutes": runtime_minutes,
                "expected_energy_kwh": round(expected_energy, 3),
                "priority": str(device.get("priority") or "normal"),
                "phase": str(device.get("phase") or "unknown"),
                "allow_grid": bool(device.get("allow_grid", False)),
                "enabled": bool(device.get("enabled", True)),
                "min_battery_soc": _number(device.get("min_battery_soc"), 0.0),
                "running": _is_running(device.get("state")),
            }
        )

    # Reserve the expected remaining session energy of loads that are already on.
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
            PRIORITY_RANK.get(item["priority"], PRIORITY_RANK["normal"]),
            str(item.get("name") or item.get("entity_id") or ""),
        ),
    )

    decisions: list[dict[str, Any]] = []
    for item in candidates:
        decision = "blocked"
        reason = "Configurazione non valida."
        would_start = False

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
                if item["nominal_power_w"] > grid_headroom_w + 1e-9:
                    decision = "blocked"
                    reason = "Margine rete insufficiente per il carico autorizzato a usare rete."
                else:
                    decision = "admissible_now"
                    reason = "Potenza, fase e budget energetico disponibili; rete consentita."
                    would_start = True
            elif item["nominal_power_w"] > remaining_solar_w + 1e-9:
                decision = "waiting_solar"
                reason = "Budget futuro disponibile, ma potenza FV disponibile ora insufficiente."
            else:
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
            if item["allow_grid"]:
                grid_headroom_w = max(
                    grid_headroom_w - item["nominal_power_w"], 0.0
                )
            else:
                remaining_solar_w = max(
                    remaining_solar_w - item["nominal_power_w"], 0.0
                )

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
                "expected_runtime_minutes": round(item["expected_runtime_minutes"], 1),
                "expected_energy_kwh": item["expected_energy_kwh"],
                "allow_grid": item["allow_grid"],
                "min_battery_soc": item["min_battery_soc"],
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
        if item["decision"] in {"waiting_solar", "waiting_energy"}
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

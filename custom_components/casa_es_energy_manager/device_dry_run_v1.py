"""v1 managed-device evaluation wrapper with runtime modes and stop policy."""

from __future__ import annotations

from typing import Any

from .const import (
    DEVICE_MODE_AUTO,
    DEVICE_MODE_OFF,
    DEVICE_MODE_OVERRIDE,
)
from .device_dry_run import (
    _inside_time_window,
    _is_running,
    _state_active,
    evaluate_managed_devices as _base_evaluate,
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _daily_limit_reached(source: dict[str, Any]) -> tuple[bool, str | None]:
    runtime = max(_number(source.get("daily_runtime_minutes")), 0.0)
    max_runtime = max(_number(source.get("max_daily_runtime_minutes")), 0.0)
    if max_runtime > 0 and runtime >= max_runtime - 1e-9:
        return True, "Tempo massimo giornaliero raggiunto."

    activations = max(_number(source.get("daily_activations")), 0.0)
    max_activations = max(_number(source.get("max_daily_activations")), 0.0)
    if max_activations > 0 and activations >= max_activations - 1e-9:
        return True, "Numero massimo di avvii giornalieri raggiunto."
    return False, None


def evaluate_managed_devices(
    devices: list[dict[str, Any]],
    *,
    data: dict[str, Any],
    policy: dict[str, Any],
    now: Any = None,
) -> dict[str, Any]:
    """Evaluate starts/stops while respecting Manuale/Spento and anti-cycling."""
    prepared: list[dict[str, Any]] = []
    manual_running_commitment = 0.0

    for original in devices:
        item = dict(original)
        mode = str(item.get("management_mode") or DEVICE_MODE_AUTO)
        shared = bool(item.get("adaptive_shared_power_sensor"))
        entity_active = _state_active(item.get("state"))
        running = _is_running(
            item.get("state"),
            item.get("current_power_w"),
            shared_power_sensor=shared,
        )
        item["entity_active"] = entity_active
        item["running"] = running

        runtime = _optional_number(item.get("expected_runtime_minutes"))
        if runtime is not None:
            runtime = max(runtime, 0.0)
        configured_power = max(_number(item.get("nominal_power_w")), 0.0)
        admission_power = max(
            _number(item.get("admission_power_w"), configured_power), 0.0
        )
        item["configured_nominal_power_w"] = configured_power
        item["nominal_power_w"] = admission_power

        min_off_minutes = max(_number(item.get("min_off_minutes"), 0.0), 0.0)
        min_off_seconds = min_off_minutes * 60.0
        legacy_interval = max(_number(item.get("switch_interval_seconds")), 0.0)
        if not entity_active:
            item["switch_interval_seconds"] = max(legacy_interval, min_off_seconds)

        limit_reached, _ = _daily_limit_reached(item)
        if mode == DEVICE_MODE_AUTO and not entity_active and limit_reached:
            item["enabled"] = False

        if mode in {DEVICE_MODE_OVERRIDE, DEVICE_MODE_OFF}:
            item["enabled"] = False
            # Manuale remains observed and its known cycle energy is reserved.
            # Spento is removed from optimization entirely.
            if mode == DEVICE_MODE_OVERRIDE and running and runtime is not None:
                manual_running_commitment += admission_power * runtime / 60_000.0
        prepared.append(item)

    adjusted_policy = dict(policy)
    budget = adjusted_policy.get("flexible_energy_budget_kwh")
    if budget is not None and manual_running_commitment > 0:
        adjusted_policy["flexible_energy_budget_kwh"] = max(
            float(budget) - manual_running_commitment, 0.0
        )

    result = _base_evaluate(prepared, data=data, policy=adjusted_policy, now=now)
    by_id = {str(item.get("subentry_id")): item for item in devices}

    # v1.4: only a measured phase/inverter overload bypasses the normal minimum
    # ON/non-interruptible rules for managed flexible loads. A total-grid warning
    # first sheds emergency-capable monitored appliances and otherwise lets
    # managed loads follow their normal anti-cycling rules.
    hard_safety = bool(data.get("phase_warning") or data.get("inverter_warning"))
    battery_soc = _number(data.get("battery_soc"))
    battery_discharge_w = max(_number(data.get("battery_discharge_w")), 0.0)
    grid_import_w = max(_number(data.get("grid_import_w")), 0.0)

    for decision in result.get("dry_run_decisions", []):
        source = by_id.get(str(decision.get("subentry_id")), {})
        mode = str(source.get("management_mode") or DEVICE_MODE_AUTO)
        entity_active = _state_active(source.get("state"))
        shared = bool(source.get("adaptive_shared_power_sensor"))
        running = _is_running(
            source.get("state"),
            source.get("current_power_w"),
            shared_power_sensor=shared,
        )

        decision["entity_active"] = entity_active
        decision["running"] = running
        decision["management_mode"] = mode
        decision["configured_nominal_power_w"] = round(
            _number(source.get("nominal_power_w")), 1
        )
        decision["admission_power_w"] = round(
            _number(
                source.get("admission_power_w"), source.get("nominal_power_w", 0)
            ),
            1,
        )
        decision["adaptive_profile"] = source.get("adaptive_profile")
        decision["daily_runtime_minutes"] = round(
            max(_number(source.get("daily_runtime_minutes")), 0.0), 1
        )
        decision["daily_activations"] = int(
            max(_number(source.get("daily_activations")), 0.0)
        )
        decision["remaining_min_daily_runtime_minutes"] = round(
            max(_number(source.get("remaining_min_daily_runtime_minutes")), 0.0), 1
        )

        min_on_minutes = max(_number(source.get("min_on_minutes"), 0.0), 0.0)
        min_off_minutes = max(_number(source.get("min_off_minutes"), 0.0), 0.0)
        elapsed = source.get("seconds_since_change")
        elapsed_seconds = _number(elapsed, 10**12) if elapsed is not None else 10**12
        decision["min_on_minutes"] = min_on_minutes
        decision["min_off_minutes"] = min_off_minutes
        decision["can_auto_stop"] = (
            not entity_active
            or min_on_minutes <= 0
            or elapsed_seconds >= min_on_minutes * 60.0
        )
        decision["would_stop"] = False
        decision["stop_is_hard_safety"] = False

        limit_reached, limit_reason = _daily_limit_reached(source)
        if mode == DEVICE_MODE_OVERRIDE:
            decision["decision"] = "manual_override"
            decision["reason"] = (
                "Modalità Manuale: Casa ES osserva il consumo ma non comanda questo dispositivo."
            )
            decision["would_start"] = False
            continue

        if mode == DEVICE_MODE_OFF:
            decision["decision"] = "forced_off"
            decision["reason"] = (
                "Modalità Spento: dispositivo escluso dalle decisioni automatiche Casa ES."
            )
            decision["would_start"] = False
            decision["would_stop"] = entity_active
            continue

        if limit_reached and not entity_active:
            decision["decision"] = "daily_limit"
            decision["reason"] = limit_reason
            decision["would_start"] = False

        if not entity_active:
            continue

        stop_reason: str | None = None
        hard_stop = False
        if hard_safety:
            stop_reason = "Protezione elettrica: richiesta riduzione immediata dei carichi flessibili."
            hard_stop = True
        elif limit_reached:
            stop_reason = limit_reason
        elif not _inside_time_window(
            now, source.get("start_after"), source.get("end_before")
        ):
            stop_reason = "Fuori dalla finestra oraria consentita: arresto automatico richiesto."
        elif battery_soc < _number(source.get("min_battery_soc"), 0.0):
            stop_reason = "SOC batteria sotto il minimo del dispositivo."
        else:
            discharge_limit = max(
                _number(source.get("battery_discharge_override_w")), 0.0
            )
            if discharge_limit > 0 and battery_discharge_w > discharge_limit + 1e-9:
                stop_reason = (
                    "Scarica batteria superiore al limite configurato per il dispositivo."
                )
            else:
                allow_grid = bool(source.get("allow_grid", False))
                max_grid = max(_number(source.get("max_grid_power_w")), 0.0)
                tolerated_grid = max_grid if allow_grid and max_grid > 0 else 100.0
                if grid_import_w > tolerated_grid + 1e-9:
                    stop_reason = "Prelievo rete superiore alla tolleranza del dispositivo."
                elif policy.get("target_reachability") == "definite_shortfall":
                    stop_reason = "Carenza energetica prevista prima del target batteria."
                elif policy.get("battery_first_preferred"):
                    stop_reason = "Margine batteria stretto: priorità temporanea alla ricarica."

        if stop_reason is None:
            if running and not decision["can_auto_stop"]:
                remaining = max(min_on_minutes * 60.0 - elapsed_seconds, 0.0) / 60.0
                decision["reason"] = (
                    f"Dispositivo attivo e protetto dal tempo minimo acceso; circa {remaining:.0f} min residui."
                )
            continue

        if hard_stop:
            decision["would_stop"] = True
            decision["stop_is_hard_safety"] = True
            decision["decision"] = "safety_stop"
            decision["reason"] = stop_reason
            decision["would_start"] = False
            continue

        if bool(source.get("on_only", False)):
            decision["decision"] = "protected_cycle"
            decision["reason"] = (
                f"{stop_reason} Ciclo non interrompibile: Casa ES attende la fine naturale."
            )
            decision["would_start"] = False
            continue

        if not decision["can_auto_stop"]:
            remaining = max(min_on_minutes * 60.0 - elapsed_seconds, 0.0) / 60.0
            decision["decision"] = "minimum_on_protected"
            decision["reason"] = (
                f"{stop_reason} Tempo minimo acceso ancora attivo: circa {remaining:.0f} min residui."
            )
            decision["would_start"] = False
            continue

        decision["would_stop"] = True
        decision["decision"] = "auto_stop"
        decision["reason"] = stop_reason
        decision["would_start"] = False

    decisions = result.get("dry_run_decisions", [])
    result["managed_devices_override"] = sum(
        1
        for item in decisions
        if item.get("management_mode") == DEVICE_MODE_OVERRIDE
    )
    result["managed_devices_forced_off"] = sum(
        1 for item in decisions if item.get("management_mode") == DEVICE_MODE_OFF
    )
    result["managed_devices_would_stop"] = sum(
        1 for item in decisions if item.get("would_stop")
    )
    result["manual_override_running_energy_commitment_kwh"] = round(
        manual_running_commitment, 3
    )
    if adjusted_policy.get("flexible_energy_budget_kwh") is not None:
        result["dry_run_remaining_flexible_budget_kwh"] = round(
            max(
                float(adjusted_policy["flexible_energy_budget_kwh"])
                - float(result.get("dry_run_running_energy_commitment_kwh") or 0.0),
                0.0,
            ),
            3,
        )
    return result

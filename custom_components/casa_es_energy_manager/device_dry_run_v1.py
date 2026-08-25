"""v1 managed-device dry-run wrapper."""

from __future__ import annotations

from typing import Any

from .const import (
    DEVICE_MODE_AUTO,
    DEVICE_MODE_OFF,
    DEVICE_MODE_OVERRIDE,
)
from .device_dry_run import evaluate_managed_devices as _base_evaluate


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


def evaluate_managed_devices(
    devices: list[dict[str, Any]],
    *,
    data: dict[str, Any],
    policy: dict[str, Any],
    now: Any = None,
) -> dict[str, Any]:
    """Evaluate automatic devices while respecting manual modes and anti-cycling."""
    prepared: list[dict[str, Any]] = []
    manual_running_commitment = 0.0

    for original in devices:
        item = dict(original)
        mode = str(item.get("management_mode") or DEVICE_MODE_AUTO)
        running = bool(item.get("running"))
        if not running:
            current_power = item.get("current_power_w")
            if current_power is not None:
                running = _number(current_power) >= 20.0
            else:
                running = str(item.get("state") or "").lower() not in {
                    "off",
                    "unknown",
                    "unavailable",
                    "none",
                    "",
                }

        runtime = _optional_number(item.get("expected_runtime_minutes"))
        if runtime is not None:
            runtime = max(runtime, 0.0)
        configured_power = max(_number(item.get("nominal_power_w")), 0.0)
        admission_power = max(
            _number(item.get("admission_power_w"), configured_power), 0.0
        )
        item["configured_nominal_power_w"] = configured_power
        item["nominal_power_w"] = admission_power

        min_off_minutes = max(
            _number(item.get("min_off_minutes"), 0.0), 0.0
        )
        min_off_seconds = min_off_minutes * 60.0
        legacy_interval = max(_number(item.get("switch_interval_seconds")), 0.0)
        if not running:
            item["switch_interval_seconds"] = max(legacy_interval, min_off_seconds)

        if mode in {DEVICE_MODE_OVERRIDE, DEVICE_MODE_OFF}:
            item["enabled"] = False
            if running and runtime is not None:
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

    for decision in result.get("dry_run_decisions", []):
        source = by_id.get(str(decision.get("subentry_id")), {})
        mode = str(source.get("management_mode") or DEVICE_MODE_AUTO)
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

        min_on_minutes = max(_number(source.get("min_on_minutes"), 0.0), 0.0)
        min_off_minutes = max(_number(source.get("min_off_minutes"), 0.0), 0.0)
        elapsed = source.get("seconds_since_change")
        elapsed_seconds = _number(elapsed, 10**12) if elapsed is not None else 10**12
        decision["min_on_minutes"] = min_on_minutes
        decision["min_off_minutes"] = min_off_minutes
        decision["can_auto_stop"] = (
            not decision.get("running")
            or min_on_minutes <= 0
            or elapsed_seconds >= min_on_minutes * 60.0
        )
        decision["would_stop"] = False

        if mode == DEVICE_MODE_OVERRIDE:
            decision["decision"] = "manual_override"
            decision["reason"] = (
                "Modalità Manuale: Casa ES osserva il consumo ma non comanda questo dispositivo."
            )
            decision["would_start"] = False
        elif mode == DEVICE_MODE_OFF:
            decision["decision"] = "forced_off"
            decision["reason"] = (
                "Modalità Spento: dispositivo escluso dalle decisioni automatiche Casa ES."
            )
            decision["would_start"] = False
            decision["would_stop"] = bool(decision.get("running"))
        elif decision.get("running") and not decision["can_auto_stop"]:
            remaining = max(min_on_minutes * 60.0 - elapsed_seconds, 0.0) / 60.0
            decision["reason"] = (
                f"Dispositivo attivo e protetto dal tempo minimo acceso; circa {remaining:.0f} min residui."
            )

    decisions = result.get("dry_run_decisions", [])
    result["managed_devices_override"] = sum(
        1
        for item in decisions
        if item.get("management_mode") == DEVICE_MODE_OVERRIDE
    )
    result["managed_devices_forced_off"] = sum(
        1 for item in decisions if item.get("management_mode") == DEVICE_MODE_OFF
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

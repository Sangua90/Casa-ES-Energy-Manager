"""Unit helpers for solar forecast inputs."""

from __future__ import annotations

from typing import Any

POWER_UNITS = {"W", "kW", "MW"}
ENERGY_UNITS = {"Wh", "kWh", "MWh"}
WINDOW_FORECAST_UNITS = POWER_UNITS | ENERGY_UNITS


def normalize_forecast_measure(
    value: Any, unit: str | None
) -> tuple[float | None, float | None]:
    """Normalize one forecast value without confusing power and energy.

    Returns ``(power_w, energy_kwh)``. Exactly one item is populated for a
    recognized unit. A power forecast is intentionally NOT converted into
    energy because that would require a known time profile or averaging rule.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, None

    if unit == "W":
        return number, None
    if unit == "kW":
        return number * 1_000.0, None
    if unit == "MW":
        return number * 1_000_000.0, None
    if unit == "Wh":
        return None, number / 1_000.0
    if unit == "kWh":
        return None, number
    if unit == "MWh":
        return None, number * 1_000.0
    return None, None

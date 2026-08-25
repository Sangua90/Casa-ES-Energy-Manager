"""Read-only phase attribution helpers for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MONITORED_LOAD_ENABLED,
    CONF_MONITORED_LOAD_NAME,
    CONF_MONITORED_LOAD_PHASE,
    CONF_MONITORED_LOAD_POWER_SENSOR,
    SUBENTRY_TYPE_MONITORED_LOAD,
)
from .phase_attribution_math import phase_attribution


def monitored_load_snapshots(
    hass: HomeAssistant,
    entry: ConfigEntry,
    numeric_power: Callable[[str | None], float | None],
) -> list[dict[str, Any]]:
    """Read configured monitored loads without ever controlling them."""
    loads: list[dict[str, Any]] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MONITORED_LOAD:
            continue
        config = dict(subentry.data)
        sensor = str(config.get(CONF_MONITORED_LOAD_POWER_SENSOR, ""))
        state = hass.states.get(sensor) if sensor else None
        power_w = numeric_power(sensor if sensor else None)
        loads.append(
            {
                **config,
                "subentry_id": subentry.subentry_id,
                "name": config.get(CONF_MONITORED_LOAD_NAME) or subentry.title,
                "power_sensor": sensor,
                "phase": str(config.get(CONF_MONITORED_LOAD_PHASE) or "unknown"),
                "enabled": bool(config.get(CONF_MONITORED_LOAD_ENABLED, True)),
                "available": bool(
                    state is not None
                    and state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
                ),
                "current_power_w": power_w,
            }
        )
    return loads


__all__ = ["monitored_load_snapshots", "phase_attribution"]

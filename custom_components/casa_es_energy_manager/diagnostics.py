"""Diagnostics support for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_GRID_POWER_LIMIT,
    CONF_GRID_POWER_SENSOR,
    CONF_INVERTER_POWER_LIMIT,
    CONF_LOAD_POWER_SENSOR,
    CONF_PHASE_L1_POWER_SENSOR,
    CONF_PHASE_L2_POWER_SENSOR,
    CONF_PHASE_L3_POWER_SENSOR,
    CONF_PHASE_POWER_LIMIT,
    CONF_PV_POWER_SENSOR,
    CONF_SAFETY_MARGIN,
    DEFAULT_GRID_POWER_LIMIT,
    DEFAULT_INVERTER_POWER_LIMIT,
    DEFAULT_PHASE_POWER_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    DOMAIN,
    VERSION,
)


def _sensor_snapshot(hass: HomeAssistant, entity_id: str | None) -> dict[str, Any] | None:
    """Return a compact snapshot of one configured source sensor."""
    if not entity_id:
        return None

    state = hass.states.get(entity_id)
    if state is None:
        return {
            "entity_id": entity_id,
            "state": None,
            "available": False,
            "reason": "entity_not_found",
        }

    available = state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    return {
        "entity_id": entity_id,
        "state": state.state,
        "unit": state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
        "available": available,
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a Casa ES Energy Manager config entry."""
    config = {**entry.data, **entry.options}
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    inputs = {
        "pv_power": _sensor_snapshot(hass, config.get(CONF_PV_POWER_SENSOR)),
        "load_power": _sensor_snapshot(hass, config.get(CONF_LOAD_POWER_SENSOR)),
        "grid_power": _sensor_snapshot(hass, config.get(CONF_GRID_POWER_SENSOR)),
        "battery_soc": _sensor_snapshot(hass, config.get(CONF_BATTERY_SOC_SENSOR)),
        "battery_power": _sensor_snapshot(hass, config.get(CONF_BATTERY_POWER_SENSOR)),
        "phase_l1_power": _sensor_snapshot(hass, config.get(CONF_PHASE_L1_POWER_SENSOR)),
        "phase_l2_power": _sensor_snapshot(hass, config.get(CONF_PHASE_L2_POWER_SENSOR)),
        "phase_l3_power": _sensor_snapshot(hass, config.get(CONF_PHASE_L3_POWER_SENSOR)),
    }

    limits = {
        "inverter_power_limit_w": float(
            config.get(CONF_INVERTER_POWER_LIMIT, DEFAULT_INVERTER_POWER_LIMIT)
        ),
        "phase_power_limit_w": float(
            config.get(CONF_PHASE_POWER_LIMIT, DEFAULT_PHASE_POWER_LIMIT)
        ),
        "grid_power_limit_w": float(
            config.get(CONF_GRID_POWER_LIMIT, DEFAULT_GRID_POWER_LIMIT)
        ),
        "safety_margin_w": float(config.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN)),
    }

    calculated: dict[str, Any] = {}
    coordinator_status: dict[str, Any] = {
        "loaded": coordinator is not None,
    }
    if coordinator is not None:
        calculated = dict(coordinator.data or {})
        coordinator_status.update(
            {
                "last_update_success": coordinator.last_update_success,
                "last_exception": (
                    str(coordinator.last_exception)
                    if coordinator.last_exception is not None
                    else None
                ),
            }
        )

    return {
        "integration": {
            "domain": DOMAIN,
            "version": VERSION,
            "read_only": True,
        },
        "source_sensors": inputs,
        "configured_limits": limits,
        "calculated_values": calculated,
        "coordinator": coordinator_status,
        "sign_conventions": {
            "grid_power": "positive = import, negative = export",
            "battery_power": "positive = charging, negative = discharging",
        },
    }

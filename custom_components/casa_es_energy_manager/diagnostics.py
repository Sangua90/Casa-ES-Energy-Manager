"""Diagnostics support for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AI_ENABLED,
    CONF_AI_INTERVAL_MINUTES,
    CONF_AI_TASK_ENTITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_BATTERY_TARGET_HOUR,
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_POWER_SENSOR,
    CONF_EXPECTED_BASE_LOAD_W,
    CONF_EXTRA_CONTEXT_SENSORS,
    CONF_GRID_POWER_LIMIT,
    CONF_GRID_POWER_SENSOR,
    CONF_INVERTER_POWER_LIMIT,
    CONF_LOAD_POWER_SENSOR,
    CONF_MONITORED_LOAD_POWER_SENSOR,
    CONF_PHASE_L1_POWER_SENSOR,
    CONF_PHASE_L2_POWER_SENSOR,
    CONF_PHASE_L3_POWER_SENSOR,
    CONF_PHASE_POWER_LIMIT,
    CONF_PV_FORECAST_CURRENT_HOUR_SENSOR,
    CONF_PV_FORECAST_NEXT_HOUR_SENSOR,
    CONF_PV_FORECAST_REMAINING_TODAY_SENSOR,
    CONF_PV_FORECAST_TODAY_SENSOR,
    CONF_PV_FORECAST_TOMORROW_SENSOR,
    CONF_PV_POTENTIAL_POWER_SENSOR,
    CONF_PV_POWER_SENSOR,
    CONF_SAFETY_MARGIN,
    CONF_WEATHER_ENTITY,
    DEFAULT_AI_ENABLED,
    DEFAULT_AI_INTERVAL_MINUTES,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
    DEFAULT_BATTERY_TARGET_HOUR,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_EXPECTED_BASE_LOAD_W,
    DEFAULT_GRID_POWER_LIMIT,
    DEFAULT_INVERTER_POWER_LIMIT,
    DEFAULT_PHASE_POWER_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    DOMAIN,
    SUBENTRY_TYPE_MANAGED_DEVICE,
    SUBENTRY_TYPE_MONITORED_LOAD,
    VERSION,
)


def _entity_snapshot(hass: HomeAssistant, entity_id: str | None) -> dict[str, Any] | None:
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
    return {
        "entity_id": entity_id,
        "state": state.state,
        "unit": state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
        "available": state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE),
        "last_changed": state.last_changed.isoformat(),
        "last_updated": state.last_updated.isoformat(),
    }


def _managed_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[dict[str, Any]]:
    """Return configured managed-load subentries with current entity snapshots."""
    result: list[dict[str, Any]] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MANAGED_DEVICE:
            continue
        config = dict(subentry.data)
        result.append(
            {
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
                "config": config,
                "entity": _entity_snapshot(
                    hass, str(config.get(CONF_DEVICE_ENTITY, "")) or None
                ),
                "power_sensor": _entity_snapshot(
                    hass,
                    str(config.get(CONF_DEVICE_POWER_SENSOR, "")) or None,
                ),
            }
        )
    return result


def _monitored_load_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> list[dict[str, Any]]:
    """Return configured read-only monitored loads and their live power sensors."""
    result: list[dict[str, Any]] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MONITORED_LOAD:
            continue
        config = dict(subentry.data)
        result.append(
            {
                "subentry_id": subentry.subentry_id,
                "title": subentry.title,
                "config": config,
                "power_sensor": _entity_snapshot(
                    hass,
                    str(config.get(CONF_MONITORED_LOAD_POWER_SENSOR, "")) or None,
                ),
                "read_only": True,
            }
        )
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    config = {**entry.data, **entry.options}
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    inputs = {
        "pv_measured_power": _entity_snapshot(hass, config.get(CONF_PV_POWER_SENSOR)),
        "pv_potential_power": _entity_snapshot(
            hass, config.get(CONF_PV_POTENTIAL_POWER_SENSOR)
        ),
        "pv_forecast_remaining_today": _entity_snapshot(
            hass, config.get(CONF_PV_FORECAST_REMAINING_TODAY_SENSOR)
        ),
        "pv_forecast_current_hour": _entity_snapshot(
            hass, config.get(CONF_PV_FORECAST_CURRENT_HOUR_SENSOR)
        ),
        "pv_forecast_next_hour": _entity_snapshot(
            hass, config.get(CONF_PV_FORECAST_NEXT_HOUR_SENSOR)
        ),
        "pv_forecast_today": _entity_snapshot(
            hass, config.get(CONF_PV_FORECAST_TODAY_SENSOR)
        ),
        "pv_forecast_tomorrow": _entity_snapshot(
            hass, config.get(CONF_PV_FORECAST_TOMORROW_SENSOR)
        ),
        "load_power": _entity_snapshot(hass, config.get(CONF_LOAD_POWER_SENSOR)),
        "grid_power": _entity_snapshot(hass, config.get(CONF_GRID_POWER_SENSOR)),
        "battery_soc": _entity_snapshot(hass, config.get(CONF_BATTERY_SOC_SENSOR)),
        "battery_power": _entity_snapshot(hass, config.get(CONF_BATTERY_POWER_SENSOR)),
        "phase_l1_power": _entity_snapshot(hass, config.get(CONF_PHASE_L1_POWER_SENSOR)),
        "phase_l2_power": _entity_snapshot(hass, config.get(CONF_PHASE_L2_POWER_SENSOR)),
        "phase_l3_power": _entity_snapshot(hass, config.get(CONF_PHASE_L3_POWER_SENSOR)),
        "weather": _entity_snapshot(hass, config.get(CONF_WEATHER_ENTITY)),
        "extra_context": [
            _entity_snapshot(hass, entity_id)
            for entity_id in (config.get(CONF_EXTRA_CONTEXT_SENSORS, []) or [])
        ],
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
        "safety_margin_w": float(
            config.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN)
        ),
    }

    ai_planner = {
        "enabled": bool(config.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED)),
        "ai_task_entity": _entity_snapshot(hass, config.get(CONF_AI_TASK_ENTITY)),
        "interval_minutes": int(
            config.get(CONF_AI_INTERVAL_MINUTES, DEFAULT_AI_INTERVAL_MINUTES)
        ),
        "battery_capacity_kwh": float(
            config.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        ),
        "battery_target_soc": float(
            config.get(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC)
        ),
        "battery_target_hour": int(
            config.get(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR)
        ),
        "expected_base_load_w": float(
            config.get(CONF_EXPECTED_BASE_LOAD_W, DEFAULT_EXPECTED_BASE_LOAD_W)
        ),
        "battery_charge_efficiency_pct": float(
            config.get(
                CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
                DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
            )
        ),
        "advisory_only": True,
    }

    calculated: dict[str, Any] = {}
    coordinator_status: dict[str, Any] = {"loaded": coordinator is not None}
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
        "integration": {"domain": DOMAIN, "version": VERSION, "read_only": True},
        "source_sensors": inputs,
        "configured_limits": limits,
        "ai_planner": ai_planner,
        "managed_devices": _managed_device_diagnostics(hass, entry),
        "monitored_loads": _monitored_load_diagnostics(hass, entry),
        "calculated_values": calculated,
        "coordinator": coordinator_status,
        "sign_conventions": {
            "grid_power": "positive = import, negative = export",
            "battery_power": "positive = charging, negative = discharging",
            "pv_measured_power": "actual inverter production",
            "pv_potential_power": "forecast/simulated unconstrained production estimate",
            "monitored_loads": "read-only attribution of measured phase power; never added on top of phase totals",
        },
    }

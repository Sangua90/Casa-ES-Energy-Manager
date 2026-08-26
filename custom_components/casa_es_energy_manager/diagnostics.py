"""Diagnostics support for Casa ES Energy Manager v1."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AI_ENABLED,
    CONF_AI_INTERVAL_MINUTES,
    CONF_AI_TASK_ENTITY,
    CONF_AUTOMATIC_REAL_LOAD_CONTROL,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_BATTERY_TARGET_HOUR,
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_MODE_CLIMATE_ENTITY,
    CONF_DEVICE_POWER_SENSOR,
    CONF_EMERGENCY_CHARGE_MAX_MINUTES,
    CONF_EMERGENCY_CHARGE_POWER_W,
    CONF_EMERGENCY_CHARGE_START_SCRIPT,
    CONF_EMERGENCY_CHARGE_STOP_SCRIPT,
    CONF_EMERGENCY_CHARGE_TARGET_SOC,
    CONF_ENERGY_PREFERENCE,
    CONF_EXPECTED_BASE_LOAD_W,
    CONF_EXTRA_CONTEXT_SENSORS,
    CONF_GRID_POWER_LIMIT,
    CONF_GRID_POWER_SENSOR,
    CONF_INVERTER_POWER_LIMIT,
    CONF_LOAD_POWER_SENSOR,
    CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
    CONF_MONITORED_LOAD_POWER_SENSOR,
    CONF_MONITORED_LOAD_RESUME_ENTITY,
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
    DEFAULT_AUTOMATIC_REAL_LOAD_CONTROL,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
    DEFAULT_BATTERY_TARGET_HOUR,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_EMERGENCY_CHARGE_MAX_MINUTES,
    DEFAULT_EMERGENCY_CHARGE_POWER_W,
    DEFAULT_EMERGENCY_CHARGE_TARGET_SOC,
    DEFAULT_ENERGY_PREFERENCE,
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
        "hvac_action": state.attributes.get("hvac_action"),
    }


def _subentries(hass: HomeAssistant, entry: ConfigEntry, kind: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != kind:
            continue
        config = dict(subentry.data)
        item: dict[str, Any] = {
            "subentry_id": subentry.subentry_id,
            "title": subentry.title,
            "config": config,
        }
        if kind == SUBENTRY_TYPE_MANAGED_DEVICE:
            item["entity"] = _entity_snapshot(
                hass, str(config.get(CONF_DEVICE_ENTITY, "")) or None
            )
            item["power_sensor"] = _entity_snapshot(
                hass, str(config.get(CONF_DEVICE_POWER_SENSOR, "")) or None
            )
            item["mode_climate_entity"] = _entity_snapshot(
                hass, str(config.get(CONF_DEVICE_MODE_CLIMATE_ENTITY, "")) or None
            )
        else:
            emergency_entity = str(
                config.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, "")
            ) or None
            resume_entity = str(
                config.get(CONF_MONITORED_LOAD_RESUME_ENTITY, "")
            ) or None
            item["power_sensor"] = _entity_snapshot(
                hass, str(config.get(CONF_MONITORED_LOAD_POWER_SENSOR, "")) or None
            )
            item["emergency_entity"] = _entity_snapshot(hass, emergency_entity)
            item["resume_entity"] = _entity_snapshot(hass, resume_entity)
            item["emergency_control_capable"] = bool(emergency_entity)
            item["read_only"] = not bool(emergency_entity)
        result.append(item)
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    config = {**entry.data, **entry.options}
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    calculated = dict(coordinator.data or {}) if coordinator is not None else {}

    source_sensors = {
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
        "phase_l1_power": _entity_snapshot(
            hass, config.get(CONF_PHASE_L1_POWER_SENSOR)
        ),
        "phase_l2_power": _entity_snapshot(
            hass, config.get(CONF_PHASE_L2_POWER_SENSOR)
        ),
        "phase_l3_power": _entity_snapshot(
            hass, config.get(CONF_PHASE_L3_POWER_SENSOR)
        ),
        "weather": _entity_snapshot(hass, config.get(CONF_WEATHER_ENTITY)),
        "extra_context": [
            _entity_snapshot(hass, entity_id)
            for entity_id in (config.get(CONF_EXTRA_CONTEXT_SENSORS, []) or [])
        ],
    }

    real_control_enabled = bool(
        calculated.get(
            "automatic_real_load_control",
            config.get(
                CONF_AUTOMATIC_REAL_LOAD_CONTROL,
                DEFAULT_AUTOMATIC_REAL_LOAD_CONTROL,
            ),
        )
    )

    return {
        "integration": {
            "domain": DOMAIN,
            "version": VERSION,
            "automatic_real_load_control": real_control_enabled,
            "manual_emergency_charge_uses_user_scripts": True,
        },
        "real_control": {
            "enabled": real_control_enabled,
            "status": calculated.get("real_control_status"),
            "last_action": calculated.get("last_real_control_action"),
            "last_entity": calculated.get("last_real_control_entity"),
            "last_reason": calculated.get("last_real_control_reason"),
            "last_at": calculated.get("last_real_control_at"),
            "last_error": calculated.get("last_real_control_error"),
            "commands_per_refresh_max": 1,
            "monitored_emergency": calculated.get("monitored_emergency_control") or {},
        },
        "source_sensors": source_sensors,
        "configured_limits": {
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
        },
        "strategy": {
            "energy_preference": config.get(
                CONF_ENERGY_PREFERENCE, DEFAULT_ENERGY_PREFERENCE
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
        },
        "ai_planner": {
            "enabled": bool(config.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED)),
            "ai_task_entity": _entity_snapshot(hass, config.get(CONF_AI_TASK_ENTITY)),
            "interval_minutes": int(
                config.get(CONF_AI_INTERVAL_MINUTES, DEFAULT_AI_INTERVAL_MINUTES)
            ),
            "advisory_only": True,
        },
        "emergency_charge": {
            "start_script": config.get(CONF_EMERGENCY_CHARGE_START_SCRIPT),
            "stop_script": config.get(CONF_EMERGENCY_CHARGE_STOP_SCRIPT),
            "target_soc": float(
                config.get(
                    CONF_EMERGENCY_CHARGE_TARGET_SOC,
                    DEFAULT_EMERGENCY_CHARGE_TARGET_SOC,
                )
            ),
            "power_w": float(
                config.get(
                    CONF_EMERGENCY_CHARGE_POWER_W,
                    DEFAULT_EMERGENCY_CHARGE_POWER_W,
                )
            ),
            "max_minutes": int(
                config.get(
                    CONF_EMERGENCY_CHARGE_MAX_MINUTES,
                    DEFAULT_EMERGENCY_CHARGE_MAX_MINUTES,
                )
            ),
            "active": calculated.get("emergency_charge_active"),
            "deadline": calculated.get("emergency_charge_deadline"),
            "last_stop_reason": calculated.get("emergency_charge_stop_reason"),
        },
        "managed_devices": _subentries(hass, entry, SUBENTRY_TYPE_MANAGED_DEVICE),
        "monitored_loads": _subentries(hass, entry, SUBENTRY_TYPE_MONITORED_LOAD),
        "runtime_managed_devices": calculated.get("managed_device_configs") or [],
        "dry_run_decisions": calculated.get("dry_run_decisions") or [],
        "adaptive_power_profiles": calculated.get("adaptive_power_profiles") or {},
        "phase_load_breakdown": calculated.get("phase_load_breakdown") or [],
        "planner_policy": calculated.get("planner_policy") or {},
        "calculated_values": calculated,
        "coordinator": {
            "loaded": coordinator is not None,
            "last_update_success": (
                coordinator.last_update_success if coordinator is not None else None
            ),
            "last_exception": (
                str(coordinator.last_exception)
                if coordinator is not None and coordinator.last_exception is not None
                else None
            ),
        },
        "sign_conventions": {
            "grid_power": "positive = import, negative = export",
            "battery_power": "positive = charging, negative = discharging",
            "pv_measured_power": "actual inverter production",
            "pv_potential_power": "forecast/simulated unconstrained production estimate",
            "phase_attribution": "individual monitored loads explain measured phase totals and are never added on top of them",
        },
    }

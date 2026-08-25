"""Config flow for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

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
    CONF_EXPECTED_BASE_LOAD_W,
    CONF_EXTRA_CONTEXT_SENSORS,
    CONF_GRID_POWER_LIMIT,
    CONF_GRID_POWER_SENSOR,
    CONF_INVERTER_POWER_LIMIT,
    CONF_LOAD_POWER_SENSOR,
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
    NAME,
)
from .forecast_units import ENERGY_UNITS, POWER_UNITS, WINDOW_FORECAST_UNITS
from .managed_device_flow import ManagedDeviceSubentrySupport

OPTIONAL_ENTITY_KEYS = (
    CONF_PHASE_L1_POWER_SENSOR,
    CONF_PHASE_L2_POWER_SENSOR,
    CONF_PHASE_L3_POWER_SENSOR,
    CONF_PV_POTENTIAL_POWER_SENSOR,
    CONF_PV_FORECAST_REMAINING_TODAY_SENSOR,
    CONF_PV_FORECAST_CURRENT_HOUR_SENSOR,
    CONF_PV_FORECAST_NEXT_HOUR_SENSOR,
    CONF_PV_FORECAST_TODAY_SENSOR,
    CONF_PV_FORECAST_TOMORROW_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_AI_TASK_ENTITY,
)


def _entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _multi_sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", multiple=True, reorder=True)
    )


def _weather_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))


def _ai_task_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task"))


def _add_optional_entity(
    fields: dict[Any, Any], key: str, current: dict[str, Any], value_selector: Any
) -> None:
    if current.get(key):
        fields[vol.Optional(key, default=current[key])] = value_selector
    else:
        fields[vol.Optional(key)] = value_selector


def _clean_options_input(user_input: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(user_input)
    for key in OPTIONAL_ENTITY_KEYS:
        if cleaned.get(key) in (None, ""):
            cleaned.pop(key, None)
    extra = cleaned.get(CONF_EXTRA_CONTEXT_SENSORS)
    cleaned[CONF_EXTRA_CONTEXT_SENSORS] = (
        [str(entity_id) for entity_id in extra if entity_id] if extra else []
    )
    return cleaned


def _validate_unit(
    hass: HomeAssistant,
    values: dict[str, Any],
    key: str,
    allowed_units: set[str],
    error_key: str,
    errors: dict[str, str],
) -> None:
    entity_id = values.get(key)
    if not entity_id:
        return
    state = hass.states.get(str(entity_id))
    if state is None:
        return
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit is not None and str(unit) not in allowed_units:
        errors[key] = error_key


def _validate_sensor_units(
    hass: HomeAssistant, values: dict[str, Any], errors: dict[str, str]
) -> None:
    for key in (
        CONF_PV_POWER_SENSOR,
        CONF_LOAD_POWER_SENSOR,
        CONF_GRID_POWER_SENSOR,
        CONF_BATTERY_POWER_SENSOR,
        CONF_PHASE_L1_POWER_SENSOR,
        CONF_PHASE_L2_POWER_SENSOR,
        CONF_PHASE_L3_POWER_SENSOR,
        CONF_PV_POTENTIAL_POWER_SENSOR,
    ):
        _validate_unit(hass, values, key, POWER_UNITS, "expected_power_sensor", errors)

    for key in (
        CONF_PV_FORECAST_CURRENT_HOUR_SENSOR,
        CONF_PV_FORECAST_NEXT_HOUR_SENSOR,
    ):
        _validate_unit(
            hass,
            values,
            key,
            WINDOW_FORECAST_UNITS,
            "expected_forecast_sensor",
            errors,
        )

    for key in (
        CONF_PV_FORECAST_REMAINING_TODAY_SENSOR,
        CONF_PV_FORECAST_TODAY_SENSOR,
        CONF_PV_FORECAST_TOMORROW_SENSOR,
    ):
        _validate_unit(hass, values, key, ENERGY_UNITS, "expected_energy_sensor", errors)


def _core_sensor_fields(current: dict[str, Any]) -> dict[Any, Any]:
    fields: dict[Any, Any] = {
        vol.Required(CONF_PV_POWER_SENSOR, default=current.get(CONF_PV_POWER_SENSOR)): _entity_selector(),
        vol.Required(CONF_LOAD_POWER_SENSOR, default=current.get(CONF_LOAD_POWER_SENSOR)): _entity_selector(),
        vol.Required(CONF_GRID_POWER_SENSOR, default=current.get(CONF_GRID_POWER_SENSOR)): _entity_selector(),
        vol.Required(CONF_BATTERY_SOC_SENSOR, default=current.get(CONF_BATTERY_SOC_SENSOR)): _entity_selector(),
        vol.Required(CONF_BATTERY_POWER_SENSOR, default=current.get(CONF_BATTERY_POWER_SENSOR)): _entity_selector(),
    }
    _add_optional_entity(fields, CONF_PHASE_L1_POWER_SENSOR, current, _entity_selector())
    _add_optional_entity(fields, CONF_PHASE_L2_POWER_SENSOR, current, _entity_selector())
    _add_optional_entity(fields, CONF_PHASE_L3_POWER_SENSOR, current, _entity_selector())
    return fields


class CasaESEnergyManagerConfigFlow(
    ManagedDeviceSubentrySupport, config_entries.ConfigFlow, domain=DOMAIN
):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            _validate_sensor_units(self.hass, user_input, errors)
            if not errors:
                return self.async_create_entry(title=NAME, data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_PV_POWER_SENSOR): _entity_selector(),
                vol.Required(CONF_LOAD_POWER_SENSOR): _entity_selector(),
                vol.Required(CONF_GRID_POWER_SENSOR): _entity_selector(),
                vol.Required(CONF_BATTERY_SOC_SENSOR): _entity_selector(),
                vol.Required(CONF_BATTERY_POWER_SENSOR): _entity_selector(),
                vol.Optional(CONF_PHASE_L1_POWER_SENSOR): _entity_selector(),
                vol.Optional(CONF_PHASE_L2_POWER_SENSOR): _entity_selector(),
                vol.Optional(CONF_PHASE_L3_POWER_SENSOR): _entity_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return CasaESEnergyManagerOptionsFlow()


class CasaESEnergyManagerOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        attempted: dict[str, Any] | None = None

        if user_input is not None:
            attempted = _clean_options_input(user_input)
            _validate_sensor_units(self.hass, attempted, errors)
            if attempted.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED):
                ai_task_entity = attempted.get(CONF_AI_TASK_ENTITY)
                if not ai_task_entity:
                    errors["base"] = "ai_task_required"
                elif not str(ai_task_entity).startswith("ai_task."):
                    errors[CONF_AI_TASK_ENTITY] = "invalid_ai_task"
                elif self.hass.states.get(str(ai_task_entity)) is None:
                    errors[CONF_AI_TASK_ENTITY] = "ai_task_not_found"
                elif not self.hass.services.has_service("ai_task", "generate_data"):
                    errors["base"] = "ai_task_unavailable"
            if not errors:
                return self.async_create_entry(data=attempted)

        current = {**self.config_entry.data, **self.config_entry.options}
        if attempted is not None:
            current.update(attempted)

        fields = _core_sensor_fields(current)
        fields.update(
            {
                vol.Required(CONF_INVERTER_POWER_LIMIT, default=current.get(CONF_INVERTER_POWER_LIMIT, DEFAULT_INVERTER_POWER_LIMIT)): vol.All(vol.Coerce(float), vol.Range(min=1000, max=30000)),
                vol.Required(CONF_PHASE_POWER_LIMIT, default=current.get(CONF_PHASE_POWER_LIMIT, DEFAULT_PHASE_POWER_LIMIT)): vol.All(vol.Coerce(float), vol.Range(min=500, max=15000)),
                vol.Required(CONF_GRID_POWER_LIMIT, default=current.get(CONF_GRID_POWER_LIMIT, DEFAULT_GRID_POWER_LIMIT)): vol.All(vol.Coerce(float), vol.Range(min=1000, max=30000)),
                vol.Required(CONF_SAFETY_MARGIN, default=current.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN)): vol.All(vol.Coerce(float), vol.Range(min=0, max=3000)),
            }
        )

        _add_optional_entity(fields, CONF_PV_POTENTIAL_POWER_SENSOR, current, _entity_selector())
        _add_optional_entity(fields, CONF_PV_FORECAST_REMAINING_TODAY_SENSOR, current, _entity_selector())
        _add_optional_entity(fields, CONF_PV_FORECAST_CURRENT_HOUR_SENSOR, current, _entity_selector())
        _add_optional_entity(fields, CONF_PV_FORECAST_NEXT_HOUR_SENSOR, current, _entity_selector())
        _add_optional_entity(fields, CONF_PV_FORECAST_TODAY_SENSOR, current, _entity_selector())
        _add_optional_entity(fields, CONF_PV_FORECAST_TOMORROW_SENSOR, current, _entity_selector())
        _add_optional_entity(fields, CONF_WEATHER_ENTITY, current, _weather_selector())
        fields[vol.Optional(CONF_EXTRA_CONTEXT_SENSORS, default=current.get(CONF_EXTRA_CONTEXT_SENSORS, []))] = _multi_sensor_selector()

        fields.update(
            {
                vol.Required(CONF_AI_ENABLED, default=current.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED)): selector.BooleanSelector(),
                vol.Required(CONF_AI_INTERVAL_MINUTES, default=current.get(CONF_AI_INTERVAL_MINUTES, DEFAULT_AI_INTERVAL_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=15, max=180)),
                vol.Required(CONF_BATTERY_CAPACITY_KWH, default=current.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)): vol.All(vol.Coerce(float), vol.Range(min=1, max=500)),
                vol.Required(CONF_BATTERY_TARGET_SOC, default=current.get(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC)): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Required(CONF_BATTERY_TARGET_HOUR, default=current.get(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR)): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                vol.Required(CONF_EXPECTED_BASE_LOAD_W, default=current.get(CONF_EXPECTED_BASE_LOAD_W, DEFAULT_EXPECTED_BASE_LOAD_W)): vol.All(vol.Coerce(float), vol.Range(min=0, max=5000)),
                vol.Required(CONF_BATTERY_CHARGE_EFFICIENCY_PCT, default=current.get(CONF_BATTERY_CHARGE_EFFICIENCY_PCT, DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT)): vol.All(vol.Coerce(float), vol.Range(min=70, max=100)),
            }
        )
        _add_optional_entity(fields, CONF_AI_TASK_ENTITY, current, _ai_task_selector())

        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields), errors=errors)

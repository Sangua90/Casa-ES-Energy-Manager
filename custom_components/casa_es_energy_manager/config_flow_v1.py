"""Multi-step configuration wizard for Casa ES Energy Manager v1."""

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
    ENERGY_PREFERENCES,
    NAME,
)
from .forecast_units import ENERGY_UNITS, POWER_UNITS, WINDOW_FORECAST_UNITS
from .subentry_support import CasaESSubentrySupport

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
    CONF_EMERGENCY_CHARGE_START_SCRIPT,
    CONF_EMERGENCY_CHARGE_STOP_SCRIPT,
)


def _sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _weather_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="weather"))


def _ai_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task"))


def _script_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="script"))


def _multi_sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", multiple=True, reorder=True)
    )


def _optional_entity(fields: dict[Any, Any], key: str, current: dict[str, Any], sel: Any) -> None:
    value = current.get(key)
    fields[vol.Optional(key, default=value) if value else vol.Optional(key)] = sel


def _unit_error(
    hass: HomeAssistant,
    values: dict[str, Any],
    key: str,
    allowed: set[str],
    errors: dict[str, str],
    error: str,
) -> None:
    entity_id = values.get(key)
    if not entity_id:
        return
    state = hass.states.get(str(entity_id))
    if state is None:
        return
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit is not None and str(unit) not in allowed:
        errors[key] = error


def _validate_sensors(hass: HomeAssistant, values: dict[str, Any], errors: dict[str, str]) -> None:
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
        _unit_error(hass, values, key, POWER_UNITS, errors, "expected_power_sensor")
    for key in (CONF_PV_FORECAST_CURRENT_HOUR_SENSOR, CONF_PV_FORECAST_NEXT_HOUR_SENSOR):
        _unit_error(hass, values, key, WINDOW_FORECAST_UNITS, errors, "expected_forecast_sensor")
    for key in (
        CONF_PV_FORECAST_REMAINING_TODAY_SENSOR,
        CONF_PV_FORECAST_TODAY_SENSOR,
        CONF_PV_FORECAST_TOMORROW_SENSOR,
    ):
        _unit_error(hass, values, key, ENERGY_UNITS, errors, "expected_energy_sensor")
    _unit_error(hass, values, CONF_BATTERY_SOC_SENSOR, {"%"}, errors, "expected_percentage_sensor")


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for key in OPTIONAL_ENTITY_KEYS:
        if result.get(key) in (None, ""):
            result.pop(key, None)
    extra = result.get(CONF_EXTRA_CONTEXT_SENSORS)
    result[CONF_EXTRA_CONTEXT_SENSORS] = list(extra or [])
    return result


def _sensor_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_PV_POWER_SENSOR, default=current.get(CONF_PV_POWER_SENSOR)): _sensor_selector(),
        vol.Required(CONF_LOAD_POWER_SENSOR, default=current.get(CONF_LOAD_POWER_SENSOR)): _sensor_selector(),
        vol.Required(CONF_GRID_POWER_SENSOR, default=current.get(CONF_GRID_POWER_SENSOR)): _sensor_selector(),
        vol.Required(CONF_BATTERY_SOC_SENSOR, default=current.get(CONF_BATTERY_SOC_SENSOR)): _sensor_selector(),
        vol.Required(CONF_BATTERY_POWER_SENSOR, default=current.get(CONF_BATTERY_POWER_SENSOR)): _sensor_selector(),
    }
    _optional_entity(fields, CONF_PHASE_L1_POWER_SENSOR, current, _sensor_selector())
    _optional_entity(fields, CONF_PHASE_L2_POWER_SENSOR, current, _sensor_selector())
    _optional_entity(fields, CONF_PHASE_L3_POWER_SENSOR, current, _sensor_selector())
    return vol.Schema(fields)


def _limits_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_INVERTER_POWER_LIMIT, default=current.get(CONF_INVERTER_POWER_LIMIT, DEFAULT_INVERTER_POWER_LIMIT)): vol.All(vol.Coerce(float), vol.Range(min=1000, max=30000)),
            vol.Required(CONF_PHASE_POWER_LIMIT, default=current.get(CONF_PHASE_POWER_LIMIT, DEFAULT_PHASE_POWER_LIMIT)): vol.All(vol.Coerce(float), vol.Range(min=500, max=15000)),
            vol.Required(CONF_GRID_POWER_LIMIT, default=current.get(CONF_GRID_POWER_LIMIT, DEFAULT_GRID_POWER_LIMIT)): vol.All(vol.Coerce(float), vol.Range(min=1000, max=30000)),
            vol.Required(CONF_SAFETY_MARGIN, default=current.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN)): vol.All(vol.Coerce(float), vol.Range(min=0, max=3000)),
        }
    )


def _forecast_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for key in (
        CONF_PV_POTENTIAL_POWER_SENSOR,
        CONF_PV_FORECAST_REMAINING_TODAY_SENSOR,
        CONF_PV_FORECAST_CURRENT_HOUR_SENSOR,
        CONF_PV_FORECAST_NEXT_HOUR_SENSOR,
        CONF_PV_FORECAST_TODAY_SENSOR,
        CONF_PV_FORECAST_TOMORROW_SENSOR,
    ):
        _optional_entity(fields, key, current, _sensor_selector())
    _optional_entity(fields, CONF_WEATHER_ENTITY, current, _weather_selector())
    return vol.Schema(fields)


def _battery_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_BATTERY_CAPACITY_KWH, default=current.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)): vol.All(vol.Coerce(float), vol.Range(min=1, max=500)),
        vol.Required(CONF_BATTERY_TARGET_SOC, default=current.get(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC)): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Required(CONF_BATTERY_TARGET_HOUR, default=current.get(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR)): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required(CONF_EXPECTED_BASE_LOAD_W, default=current.get(CONF_EXPECTED_BASE_LOAD_W, DEFAULT_EXPECTED_BASE_LOAD_W)): vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
        vol.Required(CONF_BATTERY_CHARGE_EFFICIENCY_PCT, default=current.get(CONF_BATTERY_CHARGE_EFFICIENCY_PCT, DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT)): vol.All(vol.Coerce(float), vol.Range(min=70, max=100)),
        vol.Required(CONF_ENERGY_PREFERENCE, default=current.get(CONF_ENERGY_PREFERENCE, DEFAULT_ENERGY_PREFERENCE)): selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(ENERGY_PREFERENCES), translation_key="energy_preference", mode=selector.SelectSelectorMode.DROPDOWN)
        ),
        vol.Required(CONF_EMERGENCY_CHARGE_TARGET_SOC, default=current.get(CONF_EMERGENCY_CHARGE_TARGET_SOC, DEFAULT_EMERGENCY_CHARGE_TARGET_SOC)): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Required(CONF_EMERGENCY_CHARGE_POWER_W, default=current.get(CONF_EMERGENCY_CHARGE_POWER_W, DEFAULT_EMERGENCY_CHARGE_POWER_W)): vol.All(vol.Coerce(float), vol.Range(min=100, max=15000)),
        vol.Required(CONF_EMERGENCY_CHARGE_MAX_MINUTES, default=current.get(CONF_EMERGENCY_CHARGE_MAX_MINUTES, DEFAULT_EMERGENCY_CHARGE_MAX_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
    }
    _optional_entity(fields, CONF_EMERGENCY_CHARGE_START_SCRIPT, current, _script_selector())
    _optional_entity(fields, CONF_EMERGENCY_CHARGE_STOP_SCRIPT, current, _script_selector())
    return vol.Schema(fields)


def _ai_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_AI_ENABLED, default=current.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED)): selector.BooleanSelector(),
        vol.Required(CONF_AI_INTERVAL_MINUTES, default=current.get(CONF_AI_INTERVAL_MINUTES, DEFAULT_AI_INTERVAL_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=15, max=180)),
        vol.Optional(CONF_EXTRA_CONTEXT_SENSORS, default=current.get(CONF_EXTRA_CONTEXT_SENSORS, [])): _multi_sensor_selector(),
    }
    _optional_entity(fields, CONF_AI_TASK_ENTITY, current, _ai_selector())
    return vol.Schema(fields)


def _validate_ai(hass: HomeAssistant, values: dict[str, Any], errors: dict[str, str]) -> None:
    if not values.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED):
        return
    entity = values.get(CONF_AI_TASK_ENTITY)
    if not entity:
        errors["base"] = "ai_task_required"
    elif not str(entity).startswith("ai_task."):
        errors[CONF_AI_TASK_ENTITY] = "invalid_ai_task"
    elif hass.states.get(str(entity)) is None:
        errors[CONF_AI_TASK_ENTITY] = "ai_task_not_found"
    elif not hass.services.has_service("ai_task", "generate_data"):
        errors["base"] = "ai_task_unavailable"


class CasaESEnergyManagerConfigFlow(CasaESSubentrySupport, config_entries.ConfigFlow, domain=DOMAIN):
    """Initial six-step Casa ES setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._setup_data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            _validate_sensors(self.hass, user_input, errors)
            if not errors:
                self._setup_data.update(_clean(user_input))
                return await self.async_step_limits()
        return self.async_show_form(step_id="user", data_schema=_sensor_schema({**self._setup_data, **(user_input or {})}), errors=errors)

    async def async_step_limits(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._setup_data.update(user_input)
            return await self.async_step_forecast()
        return self.async_show_form(step_id="limits", data_schema=_limits_schema(self._setup_data))

    async def async_step_forecast(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned = _clean(user_input)
            _validate_sensors(self.hass, cleaned, errors)
            if not errors:
                self._setup_data.update(cleaned)
                return await self.async_step_battery()
        return self.async_show_form(step_id="forecast", data_schema=_forecast_schema({**self._setup_data, **(user_input or {})}), errors=errors)

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._setup_data.update(_clean(user_input))
            return await self.async_step_ai()
        return self.async_show_form(step_id="battery", data_schema=_battery_schema(self._setup_data))

    async def async_step_ai(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned = _clean(user_input)
            _validate_ai(self.hass, cleaned, errors)
            if not errors:
                self._setup_data.update(cleaned)
                return await self.async_step_summary()
        return self.async_show_form(step_id="ai", data_schema=_ai_schema({**self._setup_data, **(user_input or {})}), errors=errors)

    async def async_step_summary(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=NAME, data=_clean(self._setup_data))
        return self.async_show_form(
            step_id="summary",
            data_schema=vol.Schema({}),
            description_placeholders={
                "required": "FV, carichi, rete, SOC e potenza batteria",
                "optional": "fasi, forecast, meteo, AI e script ricarica emergenza",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return CasaESEnergyManagerOptionsFlow()


class CasaESEnergyManagerOptionsFlow(OptionsFlowWithReload):
    """Edit the same sections without returning to one oversized page."""

    def __init__(self) -> None:
        self._edited: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        return {**self.config_entry.data, **self.config_entry.options, **self._edited}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_sensors(user_input)

    async def async_step_sensors(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            _validate_sensors(self.hass, user_input, errors)
            if not errors:
                self._edited.update(_clean(user_input))
                return await self.async_step_limits()
        return self.async_show_form(step_id="sensors", data_schema=_sensor_schema({**self._current(), **(user_input or {})}), errors=errors)

    async def async_step_limits(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._edited.update(user_input)
            return await self.async_step_forecast()
        return self.async_show_form(step_id="limits", data_schema=_limits_schema(self._current()))

    async def async_step_forecast(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned = _clean(user_input)
            _validate_sensors(self.hass, cleaned, errors)
            if not errors:
                self._edited.update(cleaned)
                return await self.async_step_battery()
        return self.async_show_form(step_id="forecast", data_schema=_forecast_schema({**self._current(), **(user_input or {})}), errors=errors)

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._edited.update(_clean(user_input))
            return await self.async_step_ai()
        return self.async_show_form(step_id="battery", data_schema=_battery_schema(self._current()))

    async def async_step_ai(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned = _clean(user_input)
            _validate_ai(self.hass, cleaned, errors)
            if not errors:
                self._edited.update(cleaned)
                return await self.async_step_summary()
        return self.async_show_form(step_id="ai", data_schema=_ai_schema({**self._current(), **(user_input or {})}), errors=errors)

    async def async_step_summary(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            # Options intentionally contain a full snapshot so old entries gain all v1 defaults/fields.
            return self.async_create_entry(data=_clean(self._current()))
        return self.async_show_form(step_id="summary", data_schema=vol.Schema({}))

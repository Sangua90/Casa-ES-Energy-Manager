"""v1 multi-step managed-device subentry flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_ADAPTIVE_POWER,
    CONF_DEVICE_ALLOW_GRID,
    CONF_DEVICE_AVERAGING_WINDOW_SECONDS,
    CONF_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W,
    CONF_DEVICE_BIG_CONSUMER,
    CONF_DEVICE_CURRENT_ENTITY,
    CONF_DEVICE_DYNAMIC_CURRENT,
    CONF_DEVICE_ENABLED,
    CONF_DEVICE_END_BEFORE,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_EV_CONNECTED_SENSOR,
    CONF_DEVICE_EV_SOC_SENSOR,
    CONF_DEVICE_EV_TARGET_SOC,
    CONF_DEVICE_EXPECTED_RUNTIME_MINUTES,
    CONF_DEVICE_MAX_CURRENT_A,
    CONF_DEVICE_MAX_DAILY_ACTIVATIONS,
    CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES,
    CONF_DEVICE_MAX_GRID_POWER_W,
    CONF_DEVICE_MIN_BATTERY_SOC,
    CONF_DEVICE_MIN_CURRENT_A,
    CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES,
    CONF_DEVICE_MIN_OFF_MINUTES,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_NAME,
    CONF_DEVICE_NOMINAL_POWER_W,
    CONF_DEVICE_ON_ONLY,
    CONF_DEVICE_PHASE,
    CONF_DEVICE_POWER_SENSOR,
    CONF_DEVICE_PRIORITY,
    CONF_DEVICE_PROTECT_PREEMPTION,
    CONF_DEVICE_REQUIRES_ENTITY,
    CONF_DEVICE_SCHEDULE_DEADLINE,
    CONF_DEVICE_START_AFTER,
    CONF_DEVICE_SWITCH_INTERVAL_SECONDS,
    DEFAULT_DEVICE_ADAPTIVE_POWER,
    DEFAULT_DEVICE_ALLOW_GRID,
    DEFAULT_DEVICE_AVERAGING_WINDOW_SECONDS,
    DEFAULT_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W,
    DEFAULT_DEVICE_BIG_CONSUMER,
    DEFAULT_DEVICE_DYNAMIC_CURRENT,
    DEFAULT_DEVICE_ENABLED,
    DEFAULT_DEVICE_EV_TARGET_SOC,
    DEFAULT_DEVICE_EXPECTED_RUNTIME_MINUTES,
    DEFAULT_DEVICE_MAX_CURRENT_A,
    DEFAULT_DEVICE_MAX_DAILY_ACTIVATIONS,
    DEFAULT_DEVICE_MAX_DAILY_RUNTIME_MINUTES,
    DEFAULT_DEVICE_MAX_GRID_POWER_W,
    DEFAULT_DEVICE_MIN_BATTERY_SOC,
    DEFAULT_DEVICE_MIN_CURRENT_A,
    DEFAULT_DEVICE_MIN_DAILY_RUNTIME_MINUTES,
    DEFAULT_DEVICE_MIN_OFF_MINUTES,
    DEFAULT_DEVICE_MIN_ON_MINUTES,
    DEFAULT_DEVICE_ON_ONLY,
    DEFAULT_DEVICE_PRIORITY,
    DEFAULT_DEVICE_PROTECT_PREEMPTION,
    DEVICE_PHASES,
    DEVICE_PRIORITY_MAX,
    DEVICE_PRIORITY_MIN,
    SUBENTRY_TYPE_MANAGED_DEVICE,
)
from .forecast_units import POWER_UNITS

LEGACY_PRIORITY_MAP = {"very_high": 1, "high": 3, "normal": 5, "low": 7, "very_low": 10}


def _entity(domains: str | list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))


def _managed_entity() -> selector.EntitySelector:
    return _entity(["switch", "climate", "water_heater", "fan", "input_boolean"])


def _priority(value: Any) -> int:
    if isinstance(value, str) and value in LEGACY_PRIORITY_MAP:
        return LEGACY_PRIORITY_MAP[value]
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = DEFAULT_DEVICE_PRIORITY
    return max(DEVICE_PRIORITY_MIN, min(DEVICE_PRIORITY_MAX, number))


def _optional(fields: dict[Any, Any], key: str, current: dict[str, Any], sel: Any) -> None:
    value = current.get(key)
    fields[vol.Optional(key, default=value) if value not in (None, "") else vol.Optional(key)] = sel


def _num(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _basic_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_DEVICE_NAME, default=current.get(CONF_DEVICE_NAME, "")): selector.TextSelector(),
        vol.Required(CONF_DEVICE_ENTITY, default=current.get(CONF_DEVICE_ENTITY)): _managed_entity(),
    }
    _optional(fields, CONF_DEVICE_POWER_SENSOR, current, _entity("sensor"))
    fields.update(
        {
            vol.Required(CONF_DEVICE_NOMINAL_POWER_W, default=current.get(CONF_DEVICE_NOMINAL_POWER_W, 1000)): _num(10, 15000, 10, "W"),
            vol.Required(CONF_DEVICE_ADAPTIVE_POWER, default=current.get(CONF_DEVICE_ADAPTIVE_POWER, DEFAULT_DEVICE_ADAPTIVE_POWER)): selector.BooleanSelector(),
            vol.Required(CONF_DEVICE_PRIORITY, default=_priority(current.get(CONF_DEVICE_PRIORITY, DEFAULT_DEVICE_PRIORITY))): _num(DEVICE_PRIORITY_MIN, DEVICE_PRIORITY_MAX, 1),
            vol.Required(CONF_DEVICE_PHASE, default=current.get(CONF_DEVICE_PHASE, "l1")): selector.SelectSelector(
                selector.SelectSelectorConfig(options=list(DEVICE_PHASES), translation_key="managed_phase", mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_DEVICE_EXPECTED_RUNTIME_MINUTES, default=current.get(CONF_DEVICE_EXPECTED_RUNTIME_MINUTES, DEFAULT_DEVICE_EXPECTED_RUNTIME_MINUTES)): _num(1, 1440, 5, "min"),
            vol.Required(CONF_DEVICE_MIN_ON_MINUTES, default=current.get(CONF_DEVICE_MIN_ON_MINUTES, DEFAULT_DEVICE_MIN_ON_MINUTES)): _num(0, 240, 1, "min"),
            vol.Required(CONF_DEVICE_MIN_OFF_MINUTES, default=current.get(CONF_DEVICE_MIN_OFF_MINUTES, DEFAULT_DEVICE_MIN_OFF_MINUTES)): _num(0, 240, 1, "min"),
            vol.Required(CONF_DEVICE_MIN_BATTERY_SOC, default=current.get(CONF_DEVICE_MIN_BATTERY_SOC, DEFAULT_DEVICE_MIN_BATTERY_SOC)): _num(0, 100, 1, "%"),
            vol.Required(CONF_DEVICE_ALLOW_GRID, default=current.get(CONF_DEVICE_ALLOW_GRID, DEFAULT_DEVICE_ALLOW_GRID)): selector.BooleanSelector(),
            vol.Required(CONF_DEVICE_MAX_GRID_POWER_W, default=current.get(CONF_DEVICE_MAX_GRID_POWER_W, DEFAULT_DEVICE_MAX_GRID_POWER_W)): _num(0, 10000, 50, "W"),
            vol.Required(CONF_DEVICE_ENABLED, default=current.get(CONF_DEVICE_ENABLED, DEFAULT_DEVICE_ENABLED)): selector.BooleanSelector(),
        }
    )
    return vol.Schema(fields)


def _constraints_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES, default=current.get(CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES, DEFAULT_DEVICE_MIN_DAILY_RUNTIME_MINUTES)): _num(0, 1440, 5, "min"),
        vol.Required(CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES, default=current.get(CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES, DEFAULT_DEVICE_MAX_DAILY_RUNTIME_MINUTES)): _num(0, 1440, 5, "min"),
        vol.Required(CONF_DEVICE_MAX_DAILY_ACTIVATIONS, default=current.get(CONF_DEVICE_MAX_DAILY_ACTIVATIONS, DEFAULT_DEVICE_MAX_DAILY_ACTIVATIONS)): _num(0, 100, 1),
    }
    _optional(fields, CONF_DEVICE_SCHEDULE_DEADLINE, current, selector.TimeSelector())
    _optional(fields, CONF_DEVICE_START_AFTER, current, selector.TimeSelector())
    _optional(fields, CONF_DEVICE_END_BEFORE, current, selector.TimeSelector())
    _optional(fields, CONF_DEVICE_REQUIRES_ENTITY, current, _managed_entity())
    fields.update(
        {
            vol.Required(CONF_DEVICE_AVERAGING_WINDOW_SECONDS, default=current.get(CONF_DEVICE_AVERAGING_WINDOW_SECONDS, DEFAULT_DEVICE_AVERAGING_WINDOW_SECONDS)): _num(0, 3600, 5, "s"),
            vol.Required(CONF_DEVICE_ON_ONLY, default=current.get(CONF_DEVICE_ON_ONLY, DEFAULT_DEVICE_ON_ONLY)): selector.BooleanSelector(),
            vol.Required(CONF_DEVICE_PROTECT_PREEMPTION, default=current.get(CONF_DEVICE_PROTECT_PREEMPTION, DEFAULT_DEVICE_PROTECT_PREEMPTION)): selector.BooleanSelector(),
            vol.Required(CONF_DEVICE_BIG_CONSUMER, default=current.get(CONF_DEVICE_BIG_CONSUMER, DEFAULT_DEVICE_BIG_CONSUMER)): selector.BooleanSelector(),
            vol.Required(CONF_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W, default=current.get(CONF_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W, DEFAULT_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W)): _num(0, 10000, 50, "W"),
        }
    )
    return vol.Schema(fields)


def _advanced_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_DEVICE_DYNAMIC_CURRENT, default=current.get(CONF_DEVICE_DYNAMIC_CURRENT, DEFAULT_DEVICE_DYNAMIC_CURRENT)): selector.BooleanSelector(),
    }
    _optional(fields, CONF_DEVICE_CURRENT_ENTITY, current, _entity("number"))
    fields.update(
        {
            vol.Required(CONF_DEVICE_MIN_CURRENT_A, default=current.get(CONF_DEVICE_MIN_CURRENT_A, DEFAULT_DEVICE_MIN_CURRENT_A)): _num(1, 64, 1, "A"),
            vol.Required(CONF_DEVICE_MAX_CURRENT_A, default=current.get(CONF_DEVICE_MAX_CURRENT_A, DEFAULT_DEVICE_MAX_CURRENT_A)): _num(1, 64, 1, "A"),
        }
    )
    _optional(fields, CONF_DEVICE_EV_SOC_SENSOR, current, _entity("sensor"))
    _optional(fields, CONF_DEVICE_EV_CONNECTED_SENSOR, current, _entity("binary_sensor"))
    fields[vol.Required(CONF_DEVICE_EV_TARGET_SOC, default=current.get(CONF_DEVICE_EV_TARGET_SOC, DEFAULT_DEVICE_EV_TARGET_SOC))] = _num(0, 100, 1, "%")
    return vol.Schema(fields)


class ManagedDeviceSubentryFlow(ConfigSubentryFlow):
    """Configure one managed appliance in three readable sections."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._reconfigure = False

    async def async_step_managed_device(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_basic(user_input, reconfigure=False)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_basic(user_input, reconfigure=True)

    async def _async_basic(self, user_input: dict[str, Any] | None, *, reconfigure: bool) -> SubentryFlowResult:
        self._reconfigure = reconfigure
        if reconfigure and not self._values:
            self._values.update(self._get_reconfigure_subentry().data)
        errors: dict[str, str] = {}
        if user_input is not None:
            values = dict(user_input)
            values[CONF_DEVICE_NAME] = str(values.get(CONF_DEVICE_NAME, "")).strip()
            values[CONF_DEVICE_PRIORITY] = _priority(values.get(CONF_DEVICE_PRIORITY))
            if not values[CONF_DEVICE_NAME]:
                errors[CONF_DEVICE_NAME] = "device_name_required"
            power_sensor = values.get(CONF_DEVICE_POWER_SENSOR)
            if power_sensor:
                state = self.hass.states.get(str(power_sensor))
                if state is not None:
                    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                    if unit is not None and str(unit) not in POWER_UNITS:
                        errors[CONF_DEVICE_POWER_SENSOR] = "expected_power_sensor"
            else:
                values.pop(CONF_DEVICE_POWER_SENSOR, None)

            entity_id = str(values.get(CONF_DEVICE_ENTITY, ""))
            current_id = self._get_reconfigure_subentry().subentry_id if reconfigure else None
            for subentry in self._get_entry().subentries.values():
                if subentry.subentry_type == SUBENTRY_TYPE_MANAGED_DEVICE and subentry.subentry_id != current_id and str(subentry.data.get(CONF_DEVICE_ENTITY, "")) == entity_id:
                    errors[CONF_DEVICE_ENTITY] = "device_already_configured"
                    break
            if not errors:
                self._values.update(values)
                return await self.async_step_constraints()
            self._values.update(values)
        return self.async_show_form(step_id="managed_device", data_schema=_basic_schema(self._values), errors=errors)

    async def async_step_constraints(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            values = dict(user_input)
            for key in (CONF_DEVICE_SCHEDULE_DEADLINE, CONF_DEVICE_START_AFTER, CONF_DEVICE_END_BEFORE, CONF_DEVICE_REQUIRES_ENTITY):
                if values.get(key) in (None, ""):
                    values.pop(key, None)
            if float(values.get(CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES, 0)) > float(values.get(CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES, 1440)):
                errors[CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES] = "invalid_runtime_range"
            if values.get(CONF_DEVICE_REQUIRES_ENTITY) == self._values.get(CONF_DEVICE_ENTITY):
                errors[CONF_DEVICE_REQUIRES_ENTITY] = "dependency_self"
            if not errors:
                self._values.update(values)
                return await self.async_step_advanced()
            self._values.update(values)
        return self.async_show_form(step_id="constraints", data_schema=_constraints_schema(self._values), errors=errors)

    async def async_step_advanced(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            values = dict(user_input)
            for key in (CONF_DEVICE_CURRENT_ENTITY, CONF_DEVICE_EV_SOC_SENSOR, CONF_DEVICE_EV_CONNECTED_SENSOR):
                if values.get(key) in (None, ""):
                    values.pop(key, None)
            if float(values.get(CONF_DEVICE_MIN_CURRENT_A, DEFAULT_DEVICE_MIN_CURRENT_A)) > float(values.get(CONF_DEVICE_MAX_CURRENT_A, DEFAULT_DEVICE_MAX_CURRENT_A)):
                errors[CONF_DEVICE_MAX_CURRENT_A] = "invalid_current_range"
            if values.get(CONF_DEVICE_DYNAMIC_CURRENT) and not values.get(CONF_DEVICE_CURRENT_ENTITY):
                errors[CONF_DEVICE_CURRENT_ENTITY] = "current_entity_required"
            if not errors:
                self._values.update(values)
                self._values.setdefault(CONF_DEVICE_SWITCH_INTERVAL_SECONDS, 0)
                if self._reconfigure:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        data=self._values,
                        title=str(self._values[CONF_DEVICE_NAME]),
                    )
                return self.async_create_entry(
                    title=str(self._values[CONF_DEVICE_NAME]),
                    data=self._values,
                    unique_id=str(self._values[CONF_DEVICE_ENTITY]),
                )
            self._values.update(values)
        return self.async_show_form(step_id="advanced", data_schema=_advanced_schema(self._values), errors=errors)

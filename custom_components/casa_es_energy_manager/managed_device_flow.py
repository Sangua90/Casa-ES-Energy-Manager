"""Config-subentry flow for Casa ES managed flexible loads."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_ALLOW_GRID,
    CONF_DEVICE_ENABLED,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_EXPECTED_RUNTIME_MINUTES,
    CONF_DEVICE_MIN_BATTERY_SOC,
    CONF_DEVICE_NAME,
    CONF_DEVICE_NOMINAL_POWER_W,
    CONF_DEVICE_PHASE,
    CONF_DEVICE_POWER_SENSOR,
    CONF_DEVICE_PRIORITY,
    DEFAULT_DEVICE_ALLOW_GRID,
    DEFAULT_DEVICE_ENABLED,
    DEFAULT_DEVICE_EXPECTED_RUNTIME_MINUTES,
    DEFAULT_DEVICE_MIN_BATTERY_SOC,
    DEFAULT_DEVICE_PRIORITY,
    DEVICE_PHASES,
    DEVICE_PRIORITY_MAX,
    DEVICE_PRIORITY_MIN,
    SUBENTRY_TYPE_MANAGED_DEVICE,
)
from .forecast_units import POWER_UNITS

LEGACY_PRIORITY_MAP = {
    "very_high": 1,
    "high": 3,
    "normal": 5,
    "low": 7,
    "very_low": 10,
}


class ManagedDeviceSubentrySupport:
    """Mixin exposing repeatable managed-load subentries on the main config flow."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_MANAGED_DEVICE: ManagedDeviceSubentryFlow}


def _managed_entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["switch", "climate", "water_heater", "fan", "input_boolean"]
        )
    )


def _power_sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _priority_value(value: Any) -> int:
    """Normalize numeric priority while accepting v0.4 legacy text priorities."""
    if isinstance(value, str) and value in LEGACY_PRIORITY_MAP:
        return LEGACY_PRIORITY_MAP[value]
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = DEFAULT_DEVICE_PRIORITY
    return max(DEVICE_PRIORITY_MIN, min(DEVICE_PRIORITY_MAX, number))


def _schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if current.get(CONF_DEVICE_NAME):
        name_key: Any = vol.Required(
            CONF_DEVICE_NAME, default=current[CONF_DEVICE_NAME]
        )
    else:
        name_key = vol.Required(CONF_DEVICE_NAME)
    if current.get(CONF_DEVICE_ENTITY):
        entity_key: Any = vol.Required(
            CONF_DEVICE_ENTITY, default=current[CONF_DEVICE_ENTITY]
        )
    else:
        entity_key = vol.Required(CONF_DEVICE_ENTITY)

    fields[name_key] = selector.TextSelector()
    fields[entity_key] = _managed_entity_selector()
    if current.get(CONF_DEVICE_POWER_SENSOR):
        fields[
            vol.Optional(
                CONF_DEVICE_POWER_SENSOR, default=current[CONF_DEVICE_POWER_SENSOR]
            )
        ] = _power_sensor_selector()
    else:
        fields[vol.Optional(CONF_DEVICE_POWER_SENSOR)] = _power_sensor_selector()

    fields.update(
        {
            vol.Required(
                CONF_DEVICE_NOMINAL_POWER_W,
                default=current.get(CONF_DEVICE_NOMINAL_POWER_W, 1000),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10,
                    max=15000,
                    step=10,
                    unit_of_measurement="W",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_PRIORITY,
                default=_priority_value(
                    current.get(CONF_DEVICE_PRIORITY, DEFAULT_DEVICE_PRIORITY)
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=DEVICE_PRIORITY_MIN,
                    max=DEVICE_PRIORITY_MAX,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_PHASE,
                default=current.get(CONF_DEVICE_PHASE, "l1"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(DEVICE_PHASES),
                    translation_key="managed_phase",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_DEVICE_EXPECTED_RUNTIME_MINUTES,
                default=current.get(
                    CONF_DEVICE_EXPECTED_RUNTIME_MINUTES,
                    DEFAULT_DEVICE_EXPECTED_RUNTIME_MINUTES,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=1440,
                    step=5,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_MIN_BATTERY_SOC,
                default=current.get(
                    CONF_DEVICE_MIN_BATTERY_SOC, DEFAULT_DEVICE_MIN_BATTERY_SOC
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.SLIDER,
                )
            ),
            vol.Required(
                CONF_DEVICE_ALLOW_GRID,
                default=current.get(CONF_DEVICE_ALLOW_GRID, DEFAULT_DEVICE_ALLOW_GRID),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DEVICE_ENABLED,
                default=current.get(CONF_DEVICE_ENABLED, DEFAULT_DEVICE_ENABLED),
            ): selector.BooleanSelector(),
        }
    )
    return vol.Schema(fields)


class ManagedDeviceSubentryFlow(ConfigSubentryFlow):
    """Add or edit one managed flexible load."""

    async def async_step_managed_device(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self.async_step_user(user_input)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=True)

    async def _async_form(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> SubentryFlowResult:
        current = self._get_reconfigure_subentry().data.copy() if reconfigure else {}
        errors: dict[str, str] = {}

        if user_input is not None:
            values = dict(user_input)
            values[CONF_DEVICE_NAME] = str(values.get(CONF_DEVICE_NAME, "")).strip()
            values[CONF_DEVICE_PRIORITY] = _priority_value(
                values.get(CONF_DEVICE_PRIORITY)
            )
            if not values[CONF_DEVICE_NAME]:
                errors[CONF_DEVICE_NAME] = "device_name_required"

            power_sensor = values.get(CONF_DEVICE_POWER_SENSOR)
            if power_sensor in (None, ""):
                values.pop(CONF_DEVICE_POWER_SENSOR, None)
            else:
                state = self.hass.states.get(str(power_sensor))
                if state is not None:
                    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                    if unit is not None and str(unit) not in POWER_UNITS:
                        errors[CONF_DEVICE_POWER_SENSOR] = "expected_power_sensor"

            entity_id = str(values.get(CONF_DEVICE_ENTITY, ""))
            current_id = (
                self._get_reconfigure_subentry().subentry_id if reconfigure else None
            )
            for subentry in self._get_entry().subentries.values():
                if subentry.subentry_type != SUBENTRY_TYPE_MANAGED_DEVICE:
                    continue
                if subentry.subentry_id == current_id:
                    continue
                if str(subentry.data.get(CONF_DEVICE_ENTITY, "")) == entity_id:
                    errors[CONF_DEVICE_ENTITY] = "device_already_configured"
                    break

            if not errors:
                if reconfigure:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        data=values,
                        title=values[CONF_DEVICE_NAME],
                    )
                return self.async_create_entry(
                    title=values[CONF_DEVICE_NAME],
                    data=values,
                    unique_id=entity_id,
                )
            current.update(values)

        return self.async_show_form(
            step_id="managed_device",
            data_schema=_schema(current),
            errors=errors,
        )

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
    DEFAULT_DEVICE_ON_ONLY,
    DEFAULT_DEVICE_PRIORITY,
    DEFAULT_DEVICE_PROTECT_PREEMPTION,
    DEFAULT_DEVICE_SWITCH_INTERVAL_SECONDS,
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

_OPTIONAL_ENTITY_KEYS = (
    CONF_DEVICE_POWER_SENSOR,
    CONF_DEVICE_REQUIRES_ENTITY,
    CONF_DEVICE_CURRENT_ENTITY,
    CONF_DEVICE_EV_SOC_SENSOR,
    CONF_DEVICE_EV_CONNECTED_SENSOR,
    CONF_DEVICE_SCHEDULE_DEADLINE,
    CONF_DEVICE_START_AFTER,
    CONF_DEVICE_END_BEFORE,
)


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


def _number_entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))


def _binary_sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))


def _priority_value(value: Any) -> int:
    """Normalize numeric priority while accepting v0.4 legacy text priorities."""
    if isinstance(value, str) and value in LEGACY_PRIORITY_MAP:
        return LEGACY_PRIORITY_MAP[value]
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = DEFAULT_DEVICE_PRIORITY
    return max(DEVICE_PRIORITY_MIN, min(DEVICE_PRIORITY_MAX, number))


def _optional_selector(
    fields: dict[Any, Any], key: str, current: dict[str, Any], value_selector: Any
) -> None:
    """Add an optional selector preserving its current value when reconfiguring."""
    if current.get(key) not in (None, ""):
        fields[vol.Optional(key, default=current[key])] = value_selector
    else:
        fields[vol.Optional(key)] = value_selector


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
    _optional_selector(fields, CONF_DEVICE_POWER_SENSOR, current, _power_sensor_selector())

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
                CONF_DEVICE_MAX_GRID_POWER_W,
                default=current.get(
                    CONF_DEVICE_MAX_GRID_POWER_W, DEFAULT_DEVICE_MAX_GRID_POWER_W
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=10000,
                    step=50,
                    unit_of_measurement="W",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES,
                default=current.get(
                    CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES,
                    DEFAULT_DEVICE_MIN_DAILY_RUNTIME_MINUTES,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=1440,
                    step=5,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES,
                default=current.get(
                    CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES,
                    DEFAULT_DEVICE_MAX_DAILY_RUNTIME_MINUTES,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=1440,
                    step=5,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_MAX_DAILY_ACTIVATIONS,
                default=current.get(
                    CONF_DEVICE_MAX_DAILY_ACTIVATIONS,
                    DEFAULT_DEVICE_MAX_DAILY_ACTIVATIONS,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )

    _optional_selector(
        fields, CONF_DEVICE_SCHEDULE_DEADLINE, current, selector.TimeSelector()
    )
    _optional_selector(fields, CONF_DEVICE_START_AFTER, current, selector.TimeSelector())
    _optional_selector(fields, CONF_DEVICE_END_BEFORE, current, selector.TimeSelector())
    _optional_selector(
        fields, CONF_DEVICE_REQUIRES_ENTITY, current, _managed_entity_selector()
    )

    fields.update(
        {
            vol.Required(
                CONF_DEVICE_SWITCH_INTERVAL_SECONDS,
                default=current.get(
                    CONF_DEVICE_SWITCH_INTERVAL_SECONDS,
                    DEFAULT_DEVICE_SWITCH_INTERVAL_SECONDS,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=86400,
                    step=30,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_AVERAGING_WINDOW_SECONDS,
                default=current.get(
                    CONF_DEVICE_AVERAGING_WINDOW_SECONDS,
                    DEFAULT_DEVICE_AVERAGING_WINDOW_SECONDS,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=3600,
                    step=5,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_ON_ONLY,
                default=current.get(CONF_DEVICE_ON_ONLY, DEFAULT_DEVICE_ON_ONLY),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DEVICE_PROTECT_PREEMPTION,
                default=current.get(
                    CONF_DEVICE_PROTECT_PREEMPTION,
                    DEFAULT_DEVICE_PROTECT_PREEMPTION,
                ),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DEVICE_BIG_CONSUMER,
                default=current.get(
                    CONF_DEVICE_BIG_CONSUMER, DEFAULT_DEVICE_BIG_CONSUMER
                ),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W,
                default=current.get(
                    CONF_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W,
                    DEFAULT_DEVICE_BATTERY_DISCHARGE_OVERRIDE_W,
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=10000,
                    step=50,
                    unit_of_measurement="W",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_DYNAMIC_CURRENT,
                default=current.get(
                    CONF_DEVICE_DYNAMIC_CURRENT, DEFAULT_DEVICE_DYNAMIC_CURRENT
                ),
            ): selector.BooleanSelector(),
        }
    )

    _optional_selector(
        fields, CONF_DEVICE_CURRENT_ENTITY, current, _number_entity_selector()
    )
    fields.update(
        {
            vol.Required(
                CONF_DEVICE_MIN_CURRENT_A,
                default=current.get(
                    CONF_DEVICE_MIN_CURRENT_A, DEFAULT_DEVICE_MIN_CURRENT_A
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=64,
                    step=1,
                    unit_of_measurement="A",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_DEVICE_MAX_CURRENT_A,
                default=current.get(
                    CONF_DEVICE_MAX_CURRENT_A, DEFAULT_DEVICE_MAX_CURRENT_A
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=64,
                    step=1,
                    unit_of_measurement="A",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )
    _optional_selector(fields, CONF_DEVICE_EV_SOC_SENSOR, current, _power_sensor_selector())
    _optional_selector(
        fields, CONF_DEVICE_EV_CONNECTED_SENSOR, current, _binary_sensor_selector()
    )
    fields[
        vol.Required(
            CONF_DEVICE_EV_TARGET_SOC,
            default=current.get(CONF_DEVICE_EV_TARGET_SOC, DEFAULT_DEVICE_EV_TARGET_SOC),
        )
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=100,
            step=1,
            unit_of_measurement="%",
            mode=selector.NumberSelectorMode.SLIDER,
        )
    )
    fields[
        vol.Required(
            CONF_DEVICE_ENABLED,
            default=current.get(CONF_DEVICE_ENABLED, DEFAULT_DEVICE_ENABLED),
        )
    ] = selector.BooleanSelector()
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

            for key in _OPTIONAL_ENTITY_KEYS:
                if values.get(key) in (None, ""):
                    values.pop(key, None)

            power_sensor = values.get(CONF_DEVICE_POWER_SENSOR)
            if power_sensor:
                state = self.hass.states.get(str(power_sensor))
                if state is not None:
                    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                    if unit is not None and str(unit) not in POWER_UNITS:
                        errors[CONF_DEVICE_POWER_SENSOR] = "expected_power_sensor"

            entity_id = str(values.get(CONF_DEVICE_ENTITY, ""))
            if values.get(CONF_DEVICE_REQUIRES_ENTITY) == entity_id:
                errors[CONF_DEVICE_REQUIRES_ENTITY] = "dependency_self"

            if (
                float(values.get(CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES, 0))
                > float(values.get(CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES, 1440))
            ):
                errors[CONF_DEVICE_MAX_DAILY_RUNTIME_MINUTES] = "invalid_runtime_range"

            if (
                float(values.get(CONF_DEVICE_MIN_CURRENT_A, DEFAULT_DEVICE_MIN_CURRENT_A))
                > float(values.get(CONF_DEVICE_MAX_CURRENT_A, DEFAULT_DEVICE_MAX_CURRENT_A))
            ):
                errors[CONF_DEVICE_MAX_CURRENT_A] = "invalid_current_range"

            if values.get(CONF_DEVICE_DYNAMIC_CURRENT) and not values.get(
                CONF_DEVICE_CURRENT_ENTITY
            ):
                errors[CONF_DEVICE_CURRENT_ENTITY] = "current_entity_required"

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

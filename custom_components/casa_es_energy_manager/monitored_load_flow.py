"""Config-subentry flow for Casa ES monitored loads."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.helpers import selector

from .const import (
    CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
    CONF_MONITORED_LOAD_ENABLED,
    CONF_MONITORED_LOAD_NAME,
    CONF_MONITORED_LOAD_PHASE,
    CONF_MONITORED_LOAD_POWER_SENSOR,
    CONF_MONITORED_LOAD_RESUME_ENTITY,
    DEFAULT_MONITORED_LOAD_ENABLED,
    DEVICE_PHASES,
    SUBENTRY_TYPE_MONITORED_LOAD,
)
from .forecast_units import POWER_UNITS

CONTROL_DOMAINS = [
    "switch",
    "button",
    "script",
    "input_boolean",
    "climate",
    "fan",
    "light",
]


def _power_sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _control_entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=CONTROL_DOMAINS)
    )


def _optional_entity(
    fields: dict[Any, Any], key: str, current: dict[str, Any]
) -> None:
    value = current.get(key)
    fields[vol.Optional(key, default=value) if value else vol.Optional(key)] = (
        _control_entity_selector()
    )


def _schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}

    name = current.get(CONF_MONITORED_LOAD_NAME)
    if name:
        fields[
            vol.Required(CONF_MONITORED_LOAD_NAME, default=name)
        ] = selector.TextSelector()
    else:
        fields[vol.Required(CONF_MONITORED_LOAD_NAME)] = selector.TextSelector()

    power_sensor = current.get(CONF_MONITORED_LOAD_POWER_SENSOR)
    if power_sensor:
        fields[
            vol.Required(CONF_MONITORED_LOAD_POWER_SENSOR, default=power_sensor)
        ] = _power_sensor_selector()
    else:
        fields[
            vol.Required(CONF_MONITORED_LOAD_POWER_SENSOR)
        ] = _power_sensor_selector()

    fields[
        vol.Required(
            CONF_MONITORED_LOAD_PHASE,
            default=current.get(CONF_MONITORED_LOAD_PHASE, "l1"),
        )
    ] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(DEVICE_PHASES),
            translation_key="managed_phase",
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    fields[
        vol.Required(
            CONF_MONITORED_LOAD_ENABLED,
            default=current.get(
                CONF_MONITORED_LOAD_ENABLED, DEFAULT_MONITORED_LOAD_ENABLED
            ),
        )
    ] = selector.BooleanSelector()
    _optional_entity(fields, CONF_MONITORED_LOAD_EMERGENCY_ENTITY, current)
    _optional_entity(fields, CONF_MONITORED_LOAD_RESUME_ENTITY, current)
    return vol.Schema(fields)


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    for key in (
        CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
        CONF_MONITORED_LOAD_RESUME_ENTITY,
    ):
        if result.get(key) in (None, ""):
            result.pop(key, None)
    return result


class MonitoredLoadSubentryFlow(ConfigSubentryFlow):
    """Add or edit one monitored load with optional emergency control."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a monitored load."""
        return await self._async_form(user_input, reconfigure=False, step_id="user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a monitored load."""
        return await self._async_form(
            user_input, reconfigure=True, step_id="reconfigure"
        )

    async def _async_form(
        self,
        user_input: dict[str, Any] | None,
        *,
        reconfigure: bool,
        step_id: str,
    ) -> SubentryFlowResult:
        current = self._get_reconfigure_subentry().data.copy() if reconfigure else {}
        errors: dict[str, str] = {}

        if user_input is not None:
            values = _clean(dict(user_input))
            values[CONF_MONITORED_LOAD_NAME] = str(
                values.get(CONF_MONITORED_LOAD_NAME, "")
            ).strip()
            if not values[CONF_MONITORED_LOAD_NAME]:
                errors[CONF_MONITORED_LOAD_NAME] = "monitored_name_required"

            power_sensor = str(values.get(CONF_MONITORED_LOAD_POWER_SENSOR, ""))
            state = self.hass.states.get(power_sensor) if power_sensor else None
            if state is not None:
                unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                if unit is not None and str(unit) not in POWER_UNITS:
                    errors[CONF_MONITORED_LOAD_POWER_SENSOR] = "expected_power_sensor"

            for key in (
                CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
                CONF_MONITORED_LOAD_RESUME_ENTITY,
            ):
                entity_id = str(values.get(key) or "")
                if entity_id and self.hass.states.get(entity_id) is None:
                    errors[key] = "control_entity_not_found"

            current_id = (
                self._get_reconfigure_subentry().subentry_id if reconfigure else None
            )
            for subentry in self._get_entry().subentries.values():
                if subentry.subentry_type != SUBENTRY_TYPE_MONITORED_LOAD:
                    continue
                if subentry.subentry_id == current_id:
                    continue
                if (
                    str(subentry.data.get(CONF_MONITORED_LOAD_POWER_SENSOR, ""))
                    == power_sensor
                ):
                    errors[CONF_MONITORED_LOAD_POWER_SENSOR] = (
                        "monitored_sensor_already_configured"
                    )
                    break

            if not errors:
                if reconfigure:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        data=values,
                        title=values[CONF_MONITORED_LOAD_NAME],
                    )
                return self.async_create_entry(
                    title=values[CONF_MONITORED_LOAD_NAME],
                    data=values,
                    unique_id=power_sensor,
                )
            current.update(values)

        return self.async_show_form(
            step_id=step_id,
            data_schema=_schema(current),
            errors=errors,
            last_step=True,
        )

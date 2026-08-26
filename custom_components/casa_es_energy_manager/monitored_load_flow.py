"""Config-subentry flow for Casa ES monitored loads."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.helpers import selector

from .const import (
    CONF_MONITORED_LOAD_EMERGENCY_ENABLED,
    CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
    CONF_MONITORED_LOAD_EMERGENCY_MODE,
    CONF_MONITORED_LOAD_ENABLED,
    CONF_MONITORED_LOAD_NAME,
    CONF_MONITORED_LOAD_PHASE,
    CONF_MONITORED_LOAD_POWER_SENSOR,
    CONF_MONITORED_LOAD_RESUME_ENTITY,
    DEFAULT_MONITORED_LOAD_EMERGENCY_ENABLED,
    DEFAULT_MONITORED_LOAD_ENABLED,
    DEVICE_PHASES,
    MONITORED_EMERGENCY_MODE_PAUSE_RESUME,
    MONITORED_EMERGENCY_MODE_STOP_ONLY,
    MONITORED_EMERGENCY_MODE_SWITCH,
    MONITORED_EMERGENCY_MODES,
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


def _control_entity_selector(*, switch_only: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="switch" if switch_only else CONTROL_DOMAINS)
    )


def _required_entity_schema(key: str, current: dict[str, Any], *, switch_only: bool = False) -> vol.Schema:
    value = current.get(key)
    field = vol.Required(key, default=value) if value else vol.Required(key)
    return vol.Schema({field: _control_entity_selector(switch_only=switch_only)})


def _legacy_emergency_enabled(current: dict[str, Any]) -> bool:
    if CONF_MONITORED_LOAD_EMERGENCY_ENABLED in current:
        return bool(current.get(CONF_MONITORED_LOAD_EMERGENCY_ENABLED))
    return bool(current.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY))


def _legacy_emergency_mode(current: dict[str, Any]) -> str:
    configured = str(current.get(CONF_MONITORED_LOAD_EMERGENCY_MODE) or "")
    if configured in MONITORED_EMERGENCY_MODES:
        return configured
    emergency = str(current.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY) or "")
    resume = str(current.get(CONF_MONITORED_LOAD_RESUME_ENTITY) or "")
    if emergency.startswith("switch.") and (not resume or resume == emergency):
        return MONITORED_EMERGENCY_MODE_SWITCH
    if emergency and resume:
        return MONITORED_EMERGENCY_MODE_PAUSE_RESUME
    return MONITORED_EMERGENCY_MODE_STOP_ONLY


def _base_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}

    name = current.get(CONF_MONITORED_LOAD_NAME)
    fields[vol.Required(CONF_MONITORED_LOAD_NAME, default=name) if name else vol.Required(CONF_MONITORED_LOAD_NAME)] = selector.TextSelector()

    power_sensor = current.get(CONF_MONITORED_LOAD_POWER_SENSOR)
    fields[
        vol.Required(CONF_MONITORED_LOAD_POWER_SENSOR, default=power_sensor)
        if power_sensor
        else vol.Required(CONF_MONITORED_LOAD_POWER_SENSOR)
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
            default=current.get(CONF_MONITORED_LOAD_ENABLED, DEFAULT_MONITORED_LOAD_ENABLED),
        )
    ] = selector.BooleanSelector()
    fields[
        vol.Required(
            CONF_MONITORED_LOAD_EMERGENCY_ENABLED,
            default=_legacy_emergency_enabled(current),
        )
    ] = selector.BooleanSelector()
    return vol.Schema(fields)


def _mode_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_MONITORED_LOAD_EMERGENCY_MODE,
                default=_legacy_emergency_mode(current),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(MONITORED_EMERGENCY_MODES),
                    translation_key="monitored_emergency_mode",
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


class MonitoredLoadSubentryFlow(ConfigSubentryFlow):
    """Add or edit one monitored load with optional emergency control."""

    def __init__(self) -> None:
        self._pending: dict[str, Any] = {}
        self._reconfigure = False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a monitored load."""
        self._reconfigure = False
        return await self._async_base_form(user_input, step_id="user")

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit a monitored load."""
        self._reconfigure = True
        return await self._async_base_form(user_input, step_id="reconfigure")

    def _current(self) -> dict[str, Any]:
        if self._reconfigure:
            return self._get_reconfigure_subentry().data.copy()
        return {}

    async def _async_base_form(self, user_input: dict[str, Any] | None, *, step_id: str) -> SubentryFlowResult:
        current = self._current()
        errors: dict[str, str] = {}

        if user_input is not None:
            values = dict(user_input)
            values[CONF_MONITORED_LOAD_NAME] = str(values.get(CONF_MONITORED_LOAD_NAME, "")).strip()
            if not values[CONF_MONITORED_LOAD_NAME]:
                errors[CONF_MONITORED_LOAD_NAME] = "monitored_name_required"

            power_sensor = str(values.get(CONF_MONITORED_LOAD_POWER_SENSOR, ""))
            state = self.hass.states.get(power_sensor) if power_sensor else None
            if state is not None:
                unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                if unit is not None and str(unit) not in POWER_UNITS:
                    errors[CONF_MONITORED_LOAD_POWER_SENSOR] = "expected_power_sensor"

            current_id = self._get_reconfigure_subentry().subentry_id if self._reconfigure else None
            for subentry in self._get_entry().subentries.values():
                if subentry.subentry_type != SUBENTRY_TYPE_MONITORED_LOAD or subentry.subentry_id == current_id:
                    continue
                if str(subentry.data.get(CONF_MONITORED_LOAD_POWER_SENSOR, "")) == power_sensor:
                    errors[CONF_MONITORED_LOAD_POWER_SENSOR] = "monitored_sensor_already_configured"
                    break

            if not errors:
                self._pending = current
                self._pending.update(values)
                if not bool(values.get(CONF_MONITORED_LOAD_EMERGENCY_ENABLED)):
                    self._pending.pop(CONF_MONITORED_LOAD_EMERGENCY_MODE, None)
                    self._pending.pop(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, None)
                    self._pending.pop(CONF_MONITORED_LOAD_RESUME_ENTITY, None)
                    return self._save()
                return self.async_show_form(
                    step_id="emergency_type",
                    data_schema=_mode_schema(self._pending),
                    errors={},
                    last_step=False,
                )
            current.update(values)

        return self.async_show_form(
            step_id=step_id,
            data_schema=_base_schema(current),
            errors=errors,
            last_step=not _legacy_emergency_enabled(current),
        )

    async def async_step_emergency_type(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Choose how this monitored load can be shed in an emergency."""
        if user_input is None:
            return self.async_show_form(
                step_id="emergency_type",
                data_schema=_mode_schema(self._pending),
                errors={},
                last_step=False,
            )

        mode = str(user_input.get(CONF_MONITORED_LOAD_EMERGENCY_MODE) or "")
        if mode not in MONITORED_EMERGENCY_MODES:
            return self.async_show_form(
                step_id="emergency_type",
                data_schema=_mode_schema(self._pending),
                errors={CONF_MONITORED_LOAD_EMERGENCY_MODE: "invalid_emergency_mode"},
                last_step=False,
            )
        self._pending[CONF_MONITORED_LOAD_EMERGENCY_MODE] = mode
        if mode == MONITORED_EMERGENCY_MODE_SWITCH:
            return self.async_show_form(
                step_id="emergency_switch",
                data_schema=_required_entity_schema(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, self._pending, switch_only=True),
                errors={},
                last_step=True,
            )
        if mode == MONITORED_EMERGENCY_MODE_PAUSE_RESUME:
            emergency = self._pending.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY)
            resume = self._pending.get(CONF_MONITORED_LOAD_RESUME_ENTITY)
            schema: dict[Any, Any] = {
                (vol.Required(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, default=emergency) if emergency else vol.Required(CONF_MONITORED_LOAD_EMERGENCY_ENTITY)): _control_entity_selector(),
                (vol.Required(CONF_MONITORED_LOAD_RESUME_ENTITY, default=resume) if resume else vol.Required(CONF_MONITORED_LOAD_RESUME_ENTITY)): _control_entity_selector(),
            }
            return self.async_show_form(step_id="emergency_pause_resume", data_schema=vol.Schema(schema), errors={}, last_step=True)
        return self.async_show_form(
            step_id="emergency_stop_only",
            data_schema=_required_entity_schema(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, self._pending),
            errors={},
            last_step=True,
        )

    async def async_step_emergency_switch(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_command_form(user_input, step_id="emergency_switch", require_resume=False, switch_only=True)

    async def async_step_emergency_pause_resume(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_command_form(user_input, step_id="emergency_pause_resume", require_resume=True, switch_only=False)

    async def async_step_emergency_stop_only(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_command_form(user_input, step_id="emergency_stop_only", require_resume=False, switch_only=False)

    async def _async_command_form(
        self,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        require_resume: bool,
        switch_only: bool,
    ) -> SubentryFlowResult:
        if user_input is None:
            if require_resume:
                emergency = self._pending.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY)
                resume = self._pending.get(CONF_MONITORED_LOAD_RESUME_ENTITY)
                fields: dict[Any, Any] = {
                    (vol.Required(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, default=emergency) if emergency else vol.Required(CONF_MONITORED_LOAD_EMERGENCY_ENTITY)): _control_entity_selector(),
                    (vol.Required(CONF_MONITORED_LOAD_RESUME_ENTITY, default=resume) if resume else vol.Required(CONF_MONITORED_LOAD_RESUME_ENTITY)): _control_entity_selector(),
                }
                schema = vol.Schema(fields)
            else:
                schema = _required_entity_schema(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, self._pending, switch_only=switch_only)
            return self.async_show_form(step_id=step_id, data_schema=schema, errors={}, last_step=True)

        errors: dict[str, str] = {}
        values = dict(user_input)
        keys = [CONF_MONITORED_LOAD_EMERGENCY_ENTITY]
        if require_resume:
            keys.append(CONF_MONITORED_LOAD_RESUME_ENTITY)
        for key in keys:
            entity_id = str(values.get(key) or "")
            if not entity_id or self.hass.states.get(entity_id) is None:
                errors[key] = "control_entity_not_found"
        if switch_only:
            entity_id = str(values.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY) or "")
            if entity_id and not entity_id.startswith("switch."):
                errors[CONF_MONITORED_LOAD_EMERGENCY_ENTITY] = "switch_required"

        if errors:
            self._pending.update(values)
            return await self._async_command_form(None, step_id=step_id, require_resume=require_resume, switch_only=switch_only)

        self._pending.update(values)
        if not require_resume:
            self._pending.pop(CONF_MONITORED_LOAD_RESUME_ENTITY, None)
        return self._save()

    def _save(self) -> SubentryFlowResult:
        data = dict(self._pending)
        if self._reconfigure:
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=data,
                title=str(data[CONF_MONITORED_LOAD_NAME]),
            )
        return self.async_create_entry(
            title=str(data[CONF_MONITORED_LOAD_NAME]),
            data=data,
            unique_id=str(data[CONF_MONITORED_LOAD_POWER_SENSOR]),
        )

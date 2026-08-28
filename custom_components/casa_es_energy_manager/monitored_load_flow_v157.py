"""v1.5.7 monitored-load flow with single-select pause/resume support."""

from __future__ import annotations

import unicodedata
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
    DEFAULT_MONITORED_LOAD_ENABLED,
    DEVICE_PHASES,
    MONITORED_EMERGENCY_MODE_PAUSE_RESUME,
    MONITORED_EMERGENCY_MODE_STOP_ONLY,
    MONITORED_EMERGENCY_MODE_SWITCH,
    SUBENTRY_TYPE_MONITORED_LOAD,
)
from .forecast_units import POWER_UNITS
from .monitored_load_flow import _base_schema, _command_schema

MONITORED_EMERGENCY_MODE_SELECT_PAUSE_RESUME = "select_pause_resume"
MONITORED_EMERGENCY_MODES_V157 = (
    MONITORED_EMERGENCY_MODE_SWITCH,
    MONITORED_EMERGENCY_MODE_PAUSE_RESUME,
    MONITORED_EMERGENCY_MODE_SELECT_PAUSE_RESUME,
    MONITORED_EMERGENCY_MODE_STOP_ONLY,
)

PAUSE_ALIASES = {"pause", "paused", "pausa", "in pausa"}
RESUME_ALIASES = {
    "resume",
    "riprendi",
    "run",
    "running",
    "start",
    "avvia",
    "continua",
    "continue",
    "play",
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.strip().lower().replace("_", " ").replace("-", " ").split())


def _has_alias(options: list[Any], aliases: set[str]) -> bool:
    normalized = [_norm(option) for option in options]
    for option in normalized:
        if option in aliases:
            return True
        if any(alias in option for alias in aliases if len(alias) >= 4):
            return True
    return False


def _select_supported(state: Any) -> bool:
    options = list(getattr(state, "attributes", {}).get("options") or [])
    return _has_alias(options, PAUSE_ALIASES) and _has_alias(options, RESUME_ALIASES)


def _legacy_emergency_enabled(current: dict[str, Any]) -> bool:
    if CONF_MONITORED_LOAD_EMERGENCY_ENABLED in current:
        return bool(current.get(CONF_MONITORED_LOAD_EMERGENCY_ENABLED))
    return bool(current.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY))


def _mode_schema_v157(current: dict[str, Any]) -> vol.Schema:
    configured = str(current.get(CONF_MONITORED_LOAD_EMERGENCY_MODE) or "")
    if configured not in MONITORED_EMERGENCY_MODES_V157:
        emergency = str(current.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY) or "")
        resume = str(current.get(CONF_MONITORED_LOAD_RESUME_ENTITY) or "")
        if emergency.startswith("select.") and resume == emergency:
            configured = MONITORED_EMERGENCY_MODE_SELECT_PAUSE_RESUME
        elif emergency.startswith("switch.") and (not resume or resume == emergency):
            configured = MONITORED_EMERGENCY_MODE_SWITCH
        elif emergency and resume:
            configured = MONITORED_EMERGENCY_MODE_PAUSE_RESUME
        else:
            configured = MONITORED_EMERGENCY_MODE_STOP_ONLY

    options = [
        {"value": MONITORED_EMERGENCY_MODE_SWITCH, "label": "Switch ON/OFF"},
        {
            "value": MONITORED_EMERGENCY_MODE_PAUSE_RESUME,
            "label": "Due pulsanti Pausa + Riprendi",
        },
        {
            "value": MONITORED_EMERGENCY_MODE_SELECT_PAUSE_RESUME,
            "label": "Menu a tendina Pausa + Riprendi (select)",
        },
        {"value": MONITORED_EMERGENCY_MODE_STOP_ONLY, "label": "Solo arresto"},
    ]
    return vol.Schema(
        {
            vol.Required(CONF_MONITORED_LOAD_EMERGENCY_MODE, default=configured): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def _select_command_schema(current: dict[str, Any]) -> vol.Schema:
    entity = current.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY)
    key = (
        vol.Required(CONF_MONITORED_LOAD_EMERGENCY_ENTITY, default=entity)
        if entity
        else vol.Required(CONF_MONITORED_LOAD_EMERGENCY_ENTITY)
    )
    return vol.Schema(
        {
            key: selector.EntitySelector(
                selector.EntitySelectorConfig(domain="select")
            )
        }
    )


class MonitoredLoadSubentryFlow(ConfigSubentryFlow):
    """Add/edit a monitored load, including one-entity select pause/resume."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._reconfigure = False
        self._pending: dict[str, Any] = {}
        return await self._async_base_form(user_input, step_id="user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._reconfigure = True
        self._pending = {}
        return await self._async_base_form(user_input, step_id="reconfigure")

    def _current(self) -> dict[str, Any]:
        if getattr(self, "_reconfigure", False):
            return self._get_reconfigure_subentry().data.copy()
        return {}

    async def _async_base_form(
        self,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
    ) -> SubentryFlowResult:
        current = self._current()
        errors: dict[str, str] = {}

        if user_input is not None:
            values = dict(user_input)
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

            current_id = (
                self._get_reconfigure_subentry().subentry_id
                if getattr(self, "_reconfigure", False)
                else None
            )
            for subentry in self._get_entry().subentries.values():
                if (
                    subentry.subentry_type != SUBENTRY_TYPE_MONITORED_LOAD
                    or subentry.subentry_id == current_id
                ):
                    continue
                if str(subentry.data.get(CONF_MONITORED_LOAD_POWER_SENSOR, "")) == power_sensor:
                    errors[CONF_MONITORED_LOAD_POWER_SENSOR] = "monitored_sensor_already_configured"
                    break

            if not errors:
                self._pending = current
                self._pending.update(values)
                if not bool(values.get(CONF_MONITORED_LOAD_EMERGENCY_ENABLED)):
                    for key in (
                        CONF_MONITORED_LOAD_EMERGENCY_MODE,
                        CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
                        CONF_MONITORED_LOAD_RESUME_ENTITY,
                    ):
                        self._pending.pop(key, None)
                    return self._save()
                return self.async_show_form(
                    step_id="emergency_type",
                    data_schema=_mode_schema_v157(self._pending),
                    errors={},
                    last_step=False,
                )
            current.update(values)

        return self.async_show_form(
            step_id=step_id,
            data_schema=_base_schema(current),
            errors=errors,
            last_step=False,
        )

    async def async_step_emergency_type(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="emergency_type",
                data_schema=_mode_schema_v157(self._pending),
                errors={},
                last_step=False,
            )

        mode = str(user_input.get(CONF_MONITORED_LOAD_EMERGENCY_MODE) or "")
        if mode not in MONITORED_EMERGENCY_MODES_V157:
            return self.async_show_form(
                step_id="emergency_type",
                data_schema=_mode_schema_v157(self._pending),
                errors={CONF_MONITORED_LOAD_EMERGENCY_MODE: "invalid_emergency_mode"},
                last_step=False,
            )

        self._pending[CONF_MONITORED_LOAD_EMERGENCY_MODE] = mode
        if mode == MONITORED_EMERGENCY_MODE_SELECT_PAUSE_RESUME:
            return self.async_show_form(
                step_id="emergency_pause_resume",
                data_schema=_select_command_schema(self._pending),
                errors={},
                last_step=True,
            )

        if mode == MONITORED_EMERGENCY_MODE_SWITCH:
            step_id = "emergency_switch"
        elif mode == MONITORED_EMERGENCY_MODE_PAUSE_RESUME:
            step_id = "emergency_pause_resume"
        else:
            step_id = "emergency_stop_only"
        return self.async_show_form(
            step_id=step_id,
            data_schema=_command_schema(
                self._pending,
                require_resume=mode == MONITORED_EMERGENCY_MODE_PAUSE_RESUME,
                switch_only=mode == MONITORED_EMERGENCY_MODE_SWITCH,
            ),
            errors={},
            last_step=True,
        )

    async def async_step_emergency_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_command_form(user_input, "emergency_switch", False, True)

    async def async_step_emergency_pause_resume(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if str(self._pending.get(CONF_MONITORED_LOAD_EMERGENCY_MODE) or "") == MONITORED_EMERGENCY_MODE_SELECT_PAUSE_RESUME:
            return await self._async_select_form(user_input)
        return await self._async_command_form(user_input, "emergency_pause_resume", True, False)

    async def _async_select_form(
        self, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = str(user_input.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY) or "")
            state = self.hass.states.get(entity_id) if entity_id else None
            if not entity_id or state is None or not entity_id.startswith("select."):
                errors[CONF_MONITORED_LOAD_EMERGENCY_ENTITY] = "control_entity_not_found"
            elif not _select_supported(state):
                errors[CONF_MONITORED_LOAD_EMERGENCY_ENTITY] = "control_entity_not_found"

            self._pending.update(user_input)
            if not errors:
                self._pending[CONF_MONITORED_LOAD_RESUME_ENTITY] = entity_id
                return self._save()

        return self.async_show_form(
            step_id="emergency_pause_resume",
            data_schema=_select_command_schema(self._pending),
            errors=errors,
            last_step=True,
        )

    async def async_step_emergency_stop_only(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_command_form(user_input, "emergency_stop_only", False, False)

    async def _async_command_form(
        self,
        user_input: dict[str, Any] | None,
        step_id: str,
        require_resume: bool,
        switch_only: bool,
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
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

            self._pending.update(values)
            if not errors:
                if not require_resume:
                    self._pending.pop(CONF_MONITORED_LOAD_RESUME_ENTITY, None)
                return self._save()

        return self.async_show_form(
            step_id=step_id,
            data_schema=_command_schema(
                self._pending,
                require_resume=require_resume,
                switch_only=switch_only,
            ),
            errors=errors,
            last_step=True,
        )

    def _save(self) -> SubentryFlowResult:
        data = dict(self._pending)
        if getattr(self, "_reconfigure", False):
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

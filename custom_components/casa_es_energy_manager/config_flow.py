"""Config flow for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AI_ENABLED,
    CONF_AI_INTERVAL_MINUTES,
    CONF_AI_TASK_ENTITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_BATTERY_TARGET_HOUR,
    CONF_BATTERY_TARGET_SOC,
    CONF_GRID_POWER_LIMIT,
    CONF_GRID_POWER_SENSOR,
    CONF_INVERTER_POWER_LIMIT,
    CONF_LOAD_POWER_SENSOR,
    CONF_PHASE_L1_POWER_SENSOR,
    CONF_PHASE_L2_POWER_SENSOR,
    CONF_PHASE_L3_POWER_SENSOR,
    CONF_PHASE_POWER_LIMIT,
    CONF_PV_POWER_SENSOR,
    CONF_SAFETY_MARGIN,
    DEFAULT_AI_ENABLED,
    DEFAULT_AI_INTERVAL_MINUTES,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_TARGET_HOUR,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_GRID_POWER_LIMIT,
    DEFAULT_INVERTER_POWER_LIMIT,
    DEFAULT_PHASE_POWER_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    DOMAIN,
    NAME,
)


def _entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _ai_task_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task"))


class CasaESEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Casa ES Energy Manager."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the Casa ES Energy Manager entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
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
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return CasaESEnergyManagerOptionsFlow()


class CasaESEnergyManagerOptionsFlow(OptionsFlowWithReload):
    """Handle Casa ES Energy Manager options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage protection limits and the optional AI planner."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_INVERTER_POWER_LIMIT,
                default=current.get(
                    CONF_INVERTER_POWER_LIMIT, DEFAULT_INVERTER_POWER_LIMIT
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=1000, max=30000)),
            vol.Required(
                CONF_PHASE_POWER_LIMIT,
                default=current.get(CONF_PHASE_POWER_LIMIT, DEFAULT_PHASE_POWER_LIMIT),
            ): vol.All(vol.Coerce(float), vol.Range(min=500, max=15000)),
            vol.Required(
                CONF_GRID_POWER_LIMIT,
                default=current.get(CONF_GRID_POWER_LIMIT, DEFAULT_GRID_POWER_LIMIT),
            ): vol.All(vol.Coerce(float), vol.Range(min=1000, max=30000)),
            vol.Required(
                CONF_SAFETY_MARGIN,
                default=current.get(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=3000)),
            vol.Required(
                CONF_AI_ENABLED,
                default=current.get(CONF_AI_ENABLED, DEFAULT_AI_ENABLED),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_AI_INTERVAL_MINUTES,
                default=current.get(
                    CONF_AI_INTERVAL_MINUTES, DEFAULT_AI_INTERVAL_MINUTES
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=15, max=180)),
            vol.Required(
                CONF_BATTERY_CAPACITY_KWH,
                default=current.get(
                    CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=1, max=500)),
            vol.Required(
                CONF_BATTERY_TARGET_SOC,
                default=current.get(
                    CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
            vol.Required(
                CONF_BATTERY_TARGET_HOUR,
                default=current.get(
                    CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        }
        if current.get(CONF_AI_TASK_ENTITY):
            fields[
                vol.Optional(CONF_AI_TASK_ENTITY, default=current[CONF_AI_TASK_ENTITY])
            ] = _ai_task_selector()
        else:
            fields[vol.Optional(CONF_AI_TASK_ENTITY)] = _ai_task_selector()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
        )

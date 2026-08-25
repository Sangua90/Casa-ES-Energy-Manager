"""Config flow for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
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
    DEFAULT_GRID_POWER_LIMIT,
    DEFAULT_INVERTER_POWER_LIMIT,
    DEFAULT_PHASE_POWER_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    DOMAIN,
    NAME,
)


def _entity_selector() -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


class CasaESEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Casa ES Energy Manager."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return CasaESEnergyManagerOptionsFlow(config_entry)


class CasaESEnergyManagerOptionsFlow(config_entries.OptionsFlow):
    """Handle Casa ES Energy Manager options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage protection limits."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
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
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

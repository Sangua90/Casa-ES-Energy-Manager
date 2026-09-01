"""v1.5.13 managed-device flow: optional verified multi-split climate group."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SubentryFlowResult
from homeassistant.helpers import selector

from .const import CONF_DEVICE_ENTITY, CONF_DEVICE_MODE_CLIMATE_ENTITY
from .managed_device_flow_v158 import (
    CLIMATE_DEFAULT_STOP_PERSISTENCE_MINUTES,
    CONF_DEVICE_STOP_PERSISTENCE_MINUTES,
    ManagedDeviceSubentryFlow as V158ManagedDeviceSubentryFlow,
)

CONF_DEVICE_CLIMATE_GROUP_ENTITIES = "climate_group_entities"


class ManagedDeviceSubentryFlow(V158ManagedDeviceSubentryFlow):
    """Allow one climate device to own several real split entities in Auto."""

    async def async_step_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._apply_v158_climate_defaults()
        errors: dict[str, str] = {}
        if user_input is not None:
            reference = str(user_input.get(CONF_DEVICE_MODE_CLIMATE_ENTITY) or "")
            if not reference.startswith("climate."):
                errors[CONF_DEVICE_MODE_CLIMATE_ENTITY] = "climate_reference_required"
            elif self.hass.states.get(reference) is None:
                errors[CONF_DEVICE_MODE_CLIMATE_ENTITY] = "climate_reference_not_found"

            raw_group = user_input.get(CONF_DEVICE_CLIMATE_GROUP_ENTITIES) or []
            if isinstance(raw_group, str):
                raw_group = [raw_group]
            group: list[str] = []
            for entity_id in raw_group:
                entity_id = str(entity_id or "")
                if not entity_id.startswith("climate.") or self.hass.states.get(entity_id) is None:
                    errors[CONF_DEVICE_CLIMATE_GROUP_ENTITIES] = "climate_reference_not_found"
                    break
                if entity_id not in group:
                    group.append(entity_id)

            try:
                persistence = max(float(user_input.get(CONF_DEVICE_STOP_PERSISTENCE_MINUTES, 20.0)), 0.0)
            except (TypeError, ValueError):
                persistence = CLIMATE_DEFAULT_STOP_PERSISTENCE_MINUTES

            if not errors:
                self._values[CONF_DEVICE_MODE_CLIMATE_ENTITY] = reference
                self._values[CONF_DEVICE_STOP_PERSISTENCE_MINUTES] = persistence
                if group:
                    self._values[CONF_DEVICE_CLIMATE_GROUP_ENTITIES] = group
                else:
                    self._values.pop(CONF_DEVICE_CLIMATE_GROUP_ENTITIES, None)
                return await self.async_step_constraints()

        managed = str(self._values.get(CONF_DEVICE_ENTITY) or "")
        reference = self._values.get(CONF_DEVICE_MODE_CLIMATE_ENTITY)
        if not reference and managed.startswith("climate."):
            reference = managed
        fields: dict[Any, Any] = {}
        reference_selector = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate"))
        if reference:
            fields[vol.Required(CONF_DEVICE_MODE_CLIMATE_ENTITY, default=reference)] = reference_selector
        else:
            fields[vol.Required(CONF_DEVICE_MODE_CLIMATE_ENTITY)] = reference_selector

        group_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="climate", multiple=True)
        )
        current_group = self._values.get(CONF_DEVICE_CLIMATE_GROUP_ENTITIES)
        if current_group:
            fields[vol.Optional(CONF_DEVICE_CLIMATE_GROUP_ENTITIES, default=list(current_group))] = group_selector
        else:
            fields[vol.Optional(CONF_DEVICE_CLIMATE_GROUP_ENTITIES)] = group_selector

        fields[
            vol.Required(
                CONF_DEVICE_STOP_PERSISTENCE_MINUTES,
                default=float(self._values.get(CONF_DEVICE_STOP_PERSISTENCE_MINUTES, 20.0)),
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=120, step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        )
        return self.async_show_form(step_id="climate", data_schema=vol.Schema(fields), errors=errors)

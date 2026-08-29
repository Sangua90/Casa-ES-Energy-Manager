"""v1.5.8 managed-device flow: configurable climate stop persistence."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SubentryFlowResult
from homeassistant.helpers import selector

from . import managed_device_flow_v1 as base
from .const import (
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_MIN_OFF_MINUTES,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_MODE_CLIMATE_ENTITY,
)
from .managed_device_flow_v15 import ManagedDeviceSubentryFlow as V15ManagedDeviceSubentryFlow

CONF_DEVICE_STOP_PERSISTENCE_MINUTES = "stop_persistence_minutes"
CLIMATE_DEFAULT_MIN_ON_MINUTES = 20.0
CLIMATE_DEFAULT_MIN_OFF_MINUTES = 20.0
CLIMATE_DEFAULT_STOP_PERSISTENCE_MINUTES = 20.0


class ManagedDeviceSubentryFlow(V15ManagedDeviceSubentryFlow):
    """Expose the 20/20/20 climate/PDC anti-chatter profile in configuration."""

    def _apply_v158_climate_defaults(self) -> None:
        entity_id = str(self._values.get(CONF_DEVICE_ENTITY) or "")
        if not entity_id.startswith("climate."):
            return

        try:
            min_on = float(self._values.get(CONF_DEVICE_MIN_ON_MINUTES) or 0.0)
            min_off = float(self._values.get(CONF_DEVICE_MIN_OFF_MINUTES) or 0.0)
        except (TypeError, ValueError):
            min_on = min_off = 0.0

        if min_on <= 0:
            self._values[CONF_DEVICE_MIN_ON_MINUTES] = CLIMATE_DEFAULT_MIN_ON_MINUTES
        # v1.5.7's intended default was 20/5. Convert that default profile to the
        # new 20/20 profile while leaving other explicit custom values untouched.
        if min_off <= 0 or (abs(min_on - 20.0) < 1e-9 and abs(min_off - 5.0) < 1e-9):
            self._values[CONF_DEVICE_MIN_OFF_MINUTES] = CLIMATE_DEFAULT_MIN_OFF_MINUTES
        if self._values.get(CONF_DEVICE_STOP_PERSISTENCE_MINUTES) in (None, ""):
            self._values[CONF_DEVICE_STOP_PERSISTENCE_MINUTES] = (
                CLIMATE_DEFAULT_STOP_PERSISTENCE_MINUTES
            )

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

            try:
                persistence = max(
                    float(user_input.get(CONF_DEVICE_STOP_PERSISTENCE_MINUTES, 20.0)),
                    0.0,
                )
            except (TypeError, ValueError):
                persistence = CLIMATE_DEFAULT_STOP_PERSISTENCE_MINUTES

            if not errors:
                self._values[CONF_DEVICE_MODE_CLIMATE_ENTITY] = reference
                self._values[CONF_DEVICE_STOP_PERSISTENCE_MINUTES] = persistence
                return await self.async_step_constraints()

        fields: dict[Any, Any] = {}
        managed = str(self._values.get(CONF_DEVICE_ENTITY) or "")
        reference = self._values.get(CONF_DEVICE_MODE_CLIMATE_ENTITY)
        if not reference and managed.startswith("climate."):
            reference = managed
        values = dict(self._values)
        if reference:
            values[CONF_DEVICE_MODE_CLIMATE_ENTITY] = reference
        base._required_entity(
            fields,
            CONF_DEVICE_MODE_CLIMATE_ENTITY,
            values,
            base._entity("climate"),
        )
        fields[
            vol.Required(
                CONF_DEVICE_STOP_PERSISTENCE_MINUTES,
                default=float(
                    self._values.get(
                        CONF_DEVICE_STOP_PERSISTENCE_MINUTES,
                        CLIMATE_DEFAULT_STOP_PERSISTENCE_MINUTES,
                    )
                ),
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                max=120,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        )
        return self.async_show_form(
            step_id="climate",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

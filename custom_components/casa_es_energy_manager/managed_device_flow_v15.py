"""v1.5 managed-device flow with specialized device types."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import SubentryFlowResult
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT
from homeassistant.helpers import selector

from . import managed_device_flow_v1 as base
from .const import (
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_MIN_OFF_MINUTES,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_MODE_CLIMATE_ENTITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_POWER_SENSOR,
    CONF_DEVICE_PRIORITY,
    CONF_DEVICE_SWITCH_INTERVAL_SECONDS,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_CLIMATE,
    LEGACY_REMOVED_DEVICE_KEYS,
    SUBENTRY_TYPE_MANAGED_DEVICE,
)
from .forecast_units import POWER_UNITS

DEVICE_TYPE_GENERIC = "generic"
DEVICE_TYPE_DURATION = "duration"
DEVICE_TYPE_THERMAL = "thermal_storage"
DEVICE_TYPES_V15 = (
    DEVICE_TYPE_GENERIC,
    DEVICE_TYPE_DURATION,
    DEVICE_TYPE_CLIMATE,
    DEVICE_TYPE_THERMAL,
)

# Thermal-storage keys are intentionally local to v1.5 so older releases can
# continue reading their existing subentries unchanged.
CONF_THERMAL_BASE_TEMP_C = "thermal_base_temperature_c"
CONF_THERMAL_NORMAL_MAX_TEMP_C = "thermal_normal_max_temperature_c"
CONF_THERMAL_HARD_MAX_TEMP_C = "thermal_hard_max_temperature_c"
CONF_THERMAL_BOOST_ENTITY = "thermal_boost_entity"
CONF_THERMAL_HEATING_ENTITY = "thermal_heating_entity"
CONF_THERMAL_LEGIONELLA_ENTITY = "thermal_legionella_entity"
CONF_THERMAL_AVOID_GRID_RECOVERY = "thermal_avoid_grid_recovery"
CONF_THERMAL_LEARNING = "thermal_learning"
CONF_THERMAL_STRATEGY = "thermal_strategy"
THERMAL_STRATEGIES = ("balanced", "max_solar", "comfort")

CLIMATE_DEFAULT_MIN_ON_MINUTES = 20.0
CLIMATE_DEFAULT_MIN_OFF_MINUTES = 5.0

# Reuse the mature v1 form helpers while expanding its recognized type list.
base.DEVICE_TYPES = DEVICE_TYPES_V15


def _apply_climate_cycle_profile(values: dict[str, Any]) -> None:
    """Persist the effective 20/5 climate anti-cycle profile in visible config."""
    dtype = str(values.get(CONF_DEVICE_TYPE) or DEVICE_TYPE_GENERIC)
    entity_id = str(values.get(CONF_DEVICE_ENTITY) or "")
    if dtype != DEVICE_TYPE_CLIMATE and not entity_id.startswith("climate."):
        return

    raw_on = values.get(CONF_DEVICE_MIN_ON_MINUTES)
    raw_off = values.get(CONF_DEVICE_MIN_OFF_MINUTES)
    try:
        min_on = float(raw_on) if raw_on not in (None, "") else None
        min_off = float(raw_off) if raw_off not in (None, "") else None
    except (TypeError, ValueError):
        min_on = min_off = None

    # New climate entries receive the intended compressor-safe profile.
    if min_on is None:
        values[CONF_DEVICE_MIN_ON_MINUTES] = CLIMATE_DEFAULT_MIN_ON_MINUTES
        min_on = CLIMATE_DEFAULT_MIN_ON_MINUTES
    if min_off is None:
        values[CONF_DEVICE_MIN_OFF_MINUTES] = CLIMATE_DEFAULT_MIN_OFF_MINUTES
        min_off = CLIMATE_DEFAULT_MIN_OFF_MINUTES

    # Existing v1.5 entries stored 20/20 even though v1.5.1 already applied
    # 20/5 at runtime. Convert only that exact legacy pair so explicit custom
    # values remain untouched.
    if abs(min_on - 20.0) < 1e-9 and abs(min_off - 20.0) < 1e-9:
        values[CONF_DEVICE_MIN_OFF_MINUTES] = CLIMATE_DEFAULT_MIN_OFF_MINUTES


def _thermal_schema(current: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {}

    def optional_entity(key: str, domains: list[str]) -> None:
        value = current.get(key)
        sel = selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))
        if value:
            fields[vol.Optional(key, default=value)] = sel
        else:
            fields[vol.Optional(key)] = sel

    fields[
        vol.Required(
            CONF_THERMAL_BASE_TEMP_C,
            default=current.get(CONF_THERMAL_BASE_TEMP_C, 52.0),
        )
    ] = base._num(35, 65, 0.5, "°C")
    fields[
        vol.Required(
            CONF_THERMAL_NORMAL_MAX_TEMP_C,
            default=current.get(CONF_THERMAL_NORMAL_MAX_TEMP_C, 65.0),
        )
    ] = base._num(45, 72, 0.5, "°C")
    fields[
        vol.Required(
            CONF_THERMAL_HARD_MAX_TEMP_C,
            default=current.get(CONF_THERMAL_HARD_MAX_TEMP_C, 72.0),
        )
    ] = base._num(50, 80, 0.5, "°C")

    optional_entity(CONF_THERMAL_BOOST_ENTITY, ["switch", "input_boolean"])
    optional_entity(CONF_THERMAL_HEATING_ENTITY, ["binary_sensor", "sensor"])
    optional_entity(
        CONF_THERMAL_LEGIONELLA_ENTITY,
        ["binary_sensor", "switch", "sensor"],
    )

    fields[
        vol.Required(
            CONF_THERMAL_STRATEGY,
            default=current.get(CONF_THERMAL_STRATEGY, "balanced"),
        )
    ] = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(THERMAL_STRATEGIES),
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    fields[
        vol.Required(
            CONF_THERMAL_AVOID_GRID_RECOVERY,
            default=current.get(CONF_THERMAL_AVOID_GRID_RECOVERY, True),
        )
    ] = selector.BooleanSelector()
    fields[
        vol.Required(
            CONF_THERMAL_LEARNING,
            default=current.get(CONF_THERMAL_LEARNING, True),
        )
    ] = selector.BooleanSelector()
    return vol.Schema(fields)


class ManagedDeviceSubentryFlow(base.ManagedDeviceSubentryFlow):
    """v1.5 flow adding duration and DHW thermal-storage specialization."""

    async def _async_basic(
        self,
        user_input: dict[str, Any] | None,
        *,
        reconfigure: bool,
        step_id: str,
    ) -> SubentryFlowResult:
        self._reconfigure = reconfigure
        if reconfigure and not self._values:
            self._values.update(self._get_reconfigure_subentry().data)
            base._remove_legacy_features(self._values)
            _apply_climate_cycle_profile(self._values)

        errors: dict[str, str] = {}
        if user_input is not None:
            values = base._clean_optional(dict(user_input), base.OPTIONAL_BASIC_NUMBERS)
            values[CONF_DEVICE_NAME] = str(values.get(CONF_DEVICE_NAME, "")).strip()
            values[CONF_DEVICE_PRIORITY] = base._priority(values.get(CONF_DEVICE_PRIORITY))

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

            dtype = str(values.get(CONF_DEVICE_TYPE) or DEVICE_TYPE_GENERIC)
            if dtype == DEVICE_TYPE_THERMAL and not entity_id.startswith("water_heater."):
                errors[CONF_DEVICE_ENTITY] = "thermal_water_heater_required"

            if not errors:
                self._values.update(values)
                base._remove_legacy_features(self._values)
                dtype = str(self._values.get(CONF_DEVICE_TYPE) or DEVICE_TYPE_GENERIC)
                if dtype == DEVICE_TYPE_CLIMATE:
                    _apply_climate_cycle_profile(self._values)
                    return await self.async_step_climate()
                self._values.pop(CONF_DEVICE_MODE_CLIMATE_ENTITY, None)
                if dtype == DEVICE_TYPE_THERMAL:
                    return await self.async_step_thermal()
                return await self.async_step_constraints()
            self._values.update(values)

        return self.async_show_form(
            step_id=step_id,
            data_schema=base._basic_schema(self._values),
            errors=errors,
        )

    async def async_step_thermal(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            values = dict(user_input)
            base_temp = float(values.get(CONF_THERMAL_BASE_TEMP_C, 52.0))
            normal_max = float(values.get(CONF_THERMAL_NORMAL_MAX_TEMP_C, 65.0))
            hard_max = float(values.get(CONF_THERMAL_HARD_MAX_TEMP_C, 72.0))
            if normal_max < base_temp:
                errors[CONF_THERMAL_NORMAL_MAX_TEMP_C] = "thermal_invalid_temperature_range"
            if hard_max < normal_max:
                errors[CONF_THERMAL_HARD_MAX_TEMP_C] = "thermal_invalid_temperature_range"

            for key in (
                CONF_THERMAL_BOOST_ENTITY,
                CONF_THERMAL_HEATING_ENTITY,
                CONF_THERMAL_LEGIONELLA_ENTITY,
            ):
                if values.get(key) in (None, ""):
                    values.pop(key, None)

            if not errors:
                self._values.update(values)
                # Thermal storage is never controlled by generic water_heater
                # on/off logic. v1.5 uses target temperature + Boost instead.
                self._values["on_only"] = True
                return await self.async_step_constraints()
            self._values.update(values)

        return self.async_show_form(
            step_id="thermal",
            data_schema=_thermal_schema(self._values),
            errors=errors,
        )

    async def async_step_constraints(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        result = await super().async_step_constraints(user_input)
        if user_input is not None and not getattr(result, "get", None):
            return result
        return result

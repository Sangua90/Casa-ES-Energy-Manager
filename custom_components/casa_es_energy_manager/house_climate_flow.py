"""Human-readable house and room profiles; defaults only observe."""
from homeassistant.config_entries import ConfigSubentryFlow
from homeassistant.helpers import selector
import voluptuous as vol

from .house_climate_plan import HOUSE_TYPE, ROOM_TYPE


def num(low, high, step=0.5):
    return selector.NumberSelector(selector.NumberSelectorConfig(min=low, max=high, step=step, mode=selector.NumberSelectorMode.BOX))


class HouseClimateFlow(ConfigSubentryFlow):
    VERSION = 1
    TYPE = HOUSE_TYPE

    async def async_step_user(self, user_input=None):
        return await self._form(user_input, False)

    async def async_step_reconfigure(self, user_input=None):
        return await self._form(user_input, True)

    def fields(self, current):
        fields = {}
        defaults = {"enabled": False, "reviewed": False, "season": "shoulder",
                    "electricity_price": 0.30, "gas_price": 1.0, "gas_energy_kwh_sm3": 10.7,
                    "gas_efficiency": 0.9, "economics_confirmed": False,
                    "allow_economic_grid": False, "gas_call_temperature": 26.0,
                    "hydraulics_confirmed": False, "valve_open_seconds": 180,
                    "dissipation_seconds": 180}
        for key in ("enabled", "reviewed", "economics_confirmed", "allow_economic_grid", "hydraulics_confirmed"):
            fields[vol.Required(key, default=current.get(key, defaults[key]))] = selector.BooleanSelector()
        fields[vol.Required("season", default=current.get("season", "shoulder"))] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=["winter", "shoulder", "summer"], translation_key="house_season"))
        for key, bounds in {"electricity_price": (0.01, 2, 0.01), "gas_price": (0.01, 5, 0.01),
                            "gas_energy_kwh_sm3": (5, 15, 0.1), "gas_efficiency": (0.5, 1, 0.01),
                            "gas_call_temperature": (15, 30, 0.5),
                            "valve_open_seconds": (30, 600, 15), "dissipation_seconds": (30, 600, 15)}.items():
            fields[vol.Required(key, default=current.get(key, defaults[key]))] = num(*bounds)
        marker = vol.Optional("gas_entity", default=current["gas_entity"]) if current.get("gas_entity") else vol.Optional("gas_entity")
        fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate"))
        for key, domains in (("weather_entity", ["weather"]), ("outdoor_entity", ["sensor"]), ("gas_meter_entity", ["sensor", "input_number"]), ("presence_entity", ["person", "device_tracker"])):
            marker = vol.Optional(key, default=current[key]) if current.get(key) else vol.Optional(key)
            fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domains))
        for key in ("engine_url", "engine_token"):
            marker = vol.Optional(key, default=current[key]) if current.get(key) else vol.Optional(key)
            fields[marker] = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD if key == "engine_token" else selector.TextSelectorType.TEXT))
        return fields

    async def _form(self, user_input, reconfigure):
        current = dict(self._get_reconfigure_subentry().data) if reconfigure else {}
        errors = {}
        if user_input is not None:
            values = {**current, **user_input}
            if self.TYPE == HOUSE_TYPE:
                if any(s.subentry_type == HOUSE_TYPE and (not reconfigure or s.subentry_id != self._get_reconfigure_subentry().subentry_id)
                       for s in self._get_entry().subentries.values()):
                    errors["base"] = "house_already_configured"
                gas = self.hass.states.get(values.get("gas_entity", ""))
                if values.get("gas_entity") and (gas is None or "heat" not in gas.attributes.get("hvac_modes", [])):
                    errors["gas_entity"] = "heating_entity_required"
                title = "Clima casa"
            else:
                title = str(values.get("name", "")).strip()
                if not title:
                    errors["name"] = "name_required"
                hp = self.hass.states.get(values.get("heat_pump_entity", ""))
                if values.get("heat_pump_entity") and (hp is None or "heat" not in hp.attributes.get("hvac_modes", [])):
                    errors["heat_pump_entity"] = "heating_entity_required"
                if values["base_temperature"] > values["comfort_temperature"] or values.get("maintenance_temperature", 17) > values["comfort_temperature"]:
                    errors["base_temperature"] = "temperature_range"
                entities = set(values.get("radiator_entities") or []) | {values.get("heat_pump_entity", "")}
                entities.discard("")
                for s in self._get_entry().subentries.values():
                    if s.subentry_type == HOUSE_TYPE and s.data.get("gas_entity") in entities:
                        errors["base"] = "duplicate_room_entity"
                    if s.subentry_type != ROOM_TYPE or (reconfigure and s.subentry_id == self._get_reconfigure_subentry().subentry_id):
                        continue
                    other = set(s.data.get("radiator_entities") or []) | {s.data.get("heat_pump_entity", "")}
                    if entities & other:
                        errors["base"] = "duplicate_room_entity"
                for entity in values.get("radiator_entities") or []:
                    state = self.hass.states.get(entity)
                    if state is None or "heat" not in state.attributes.get("hvac_modes", []):
                        errors["radiator_entities"] = "heating_entity_required"
            if not errors:
                if reconfigure:
                    return self.async_update_and_abort(self._get_entry(), self._get_reconfigure_subentry(), data=values, title=title)
                return self.async_create_entry(title=title, data=values)
            current.update(values)
        return self.async_show_form(step_id="reconfigure" if reconfigure else "user", data_schema=vol.Schema(self.fields(current)), errors=errors)


class ClimateRoomFlow(HouseClimateFlow):
    TYPE = ROOM_TYPE

    def fields(self, current):
        fields = {vol.Required("name", default=current.get("name", "")): selector.TextSelector()}
        for key, domains, multiple in (("radiator_entities", ["climate"], True), ("heat_pump_entity", ["climate"], False),
                                        ("temperature_entity", ["sensor"], False), ("power_entity", ["sensor"], False), ("window_entity", ["binary_sensor"], False), ("window_entities", ["binary_sensor"], True),
                                        ("contamination_entities", ["climate", "binary_sensor", "sensor"], True),
                                        ("neighbor_temperature_entities", ["climate", "sensor"], True)):
            marker = vol.Optional(key, default=current[key]) if current.get(key) else vol.Optional(key)
            fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domains, multiple=multiple))
        for key, default, bounds in (("maintenance_temperature", 17, (10, 22, .5)), ("day_base_temperature", 19, (10, 22, .5)), ("priority", 2, (1, 5, 1)), ("base_temperature", 17, (10, 22, .5)), ("comfort_temperature", 21, (16, 22, .5)),
                                     ("cooling_temperature", 26, (20, 30, .5)), ("preheat_minutes", 90, (0, 480, 5)),
                                     ("heat_pump_cop", 3, (1, 7, .1)), ("nominal_power_w", 1200, (100, 10000, 50))):
            fields[vol.Required(key, default=current.get(key, default))] = num(*bounds)
        for key in ("reviewed", "manual_only", "cop_confirmed", "base_enabled"):
            fields[vol.Required(key, default=current.get(key, False))] = selector.BooleanSelector()
        fields[vol.Required("machine", default=current.get("machine", "none"))] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=["none", "salotto", "ester", "p1"], translation_key="house_machine"))
        fields[vol.Required("phase", default=current.get("phase", "unknown"))] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=["unknown", "l1", "l2", "l3"], translation_key="house_phase"))
        for prefix in ("weekday", "weekday_second", "weekend", "weekend_second"):
            fields[vol.Required(prefix + "_enabled", default=current.get(prefix + "_enabled", False))] = selector.BooleanSelector()
            for key in ("start", "end"):
                fields[vol.Required(prefix + "_" + key, default=current.get(prefix + "_" + key, "00:00:00"))] = selector.TimeSelector()
        fields[vol.Required("weekday_early_days", default=current.get("weekday_early_days", []))] = selector.SelectSelector(selector.SelectSelectorConfig(options=["0", "1", "2", "3", "4"], multiple=True, translation_key="house_weekdays"))
        fields[vol.Required("weekday_early_start", default=current.get("weekday_early_start", "16:30:00"))] = selector.TimeSelector()
        return fields

"""Optional combined gas/radiator and room heat-pump management."""
from datetime import timedelta
from functools import partial
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.helpers.recorder import get_instance
from .climate_history import replay, celsius
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .coordinator_v1520 import CasaESEnergyCoordinator as PreviousCoordinator
from .house_climate_plan import HOUSE_TYPE, ROOM_TYPE, finite, room_plan, situation, auxiliary_active
from .engine_bridge import async_plan, async_import_history
from homeassistant.components.recorder.statistics import statistics_during_period
from .pv_local_nowcast import fit as fit_pv, estimate as estimate_pv, combined as combine_pv

_LOGGER = logging.getLogger(__name__)


class CasaESEnergyCoordinator(PreviousCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self._pv_model = {}
        self._pv_next_learning = 0
        self._pv_estimate = None
        self._pv_combined = None
        self._pv_info = {}
        self._pv_store = Store(hass, 1, f"casa_es_energy_manager.{entry.entry_id}.pv_local_nowcast")
        self._house_reference_offsets = {}
        self._house_owned = {}
        self._house_hold = {}
        self._house_error = None
        self.house_gas_mode = "off"
        self.house_machine_modes = {"salotto": "manual", "ester": "manual", "p1": "manual"}
        self._house_open_since = {}
        self._house_gas_stopped_at = 0
        self._house_engine_result = {}
        self._house_last_start = 0
        self._house_history_until = 0
        self._house_history_status = "pending"
        self._house_history_task = None
        self._house_exception = {}
        self._house_last_hvac_at = 0
        self._house_store = Store(hass, 1, f"casa_es_energy_manager.{entry.entry_id}.house_climate_control")

    async def async_initialize(self):
        pv_saved = await self._pv_store.async_load()
        self._pv_model = pv_saved if isinstance(pv_saved, dict) else {}
        saved = await self._house_store.async_load()
        if isinstance(saved, dict):
            self._house_reference_offsets = saved.get("reference_offsets", {})
            self._house_owned = saved.get("owned", {})
            self._house_history_until = saved.get("history_until", 0)
            self._house_exception = saved.get("exception", {})
            self._house_hold = saved.get("hold", {})
            self.house_gas_mode = "auto" if saved.get("gas_mode") == "auto" else "off"
            self._house_last_start = saved.get("last_start", 0)
            self.house_machine_modes.update(saved.get("machine_modes", {}))
            self._house_gas_stopped_at = saved.get("gas_stopped_at", 0)
        await super().async_initialize()
        self._house_history_task = self.hass.async_create_background_task(self._import_climate_history(), "Energy Meter climate history")

    def _pv_capacity(self):
        entries = self.hass.config_entries.async_entries("open_meteo_solar_forecast")
        if entries:
            power = entries[0].options.get("modules_power", 8720)
            values = power if isinstance(power, (list, tuple)) else [power]
            return max(sum(finite(value, 0) for value in values), 1)
        return 8720.0

    def _numeric_state(self, entity_id, *, power=False, energy=False):
        value = super()._numeric_state(entity_id, power=power, energy=energy)
        if (power and entity_id and entity_id == self._config("pv_potential_power_sensor")
                and getattr(self, "_pv_combined", None) is not None):
            return self._pv_combined
        return value

    async def _refresh_pv_nowcast(self, now):
        nominal = self._pv_capacity()
        selector = self.hass.states.get("input_boolean.energy_manager_stima_fv_locale")
        layout = self.hass.states.get("input_datetime.energy_manager_pannelli_attuali_dal")
        layout_since = layout.state if layout else None
        try:
            valid_layout = bool(layout_since and dt_util.parse_datetime(layout_since + "T00:00:00")
                                and layout_since <= now.date().isoformat())
        except (ValueError, TypeError):
            valid_layout = False
        enabled = bool(selector and selector.state == "on" and valid_layout)
        if self._pv_info.get("layout_since") != layout_since:
            self._pv_next_learning = 0
        if self._pv_model.get("layout_since") != layout_since:
            self._pv_model = {}
        roles = {"actual": self._config("pv_power_sensor"),
                 "soc": self._config("battery_soc_sensor"),
                 "forecast": "sensor.casa_es_power_production_now",
                 "irradiance": "sensor.stazione_meteo_solar_radiation"}
        for role, entity in {"string1": "sensor.inverter_solarman_pv_power_1",
                             "string2": "sensor.inverter_solarman_pv_power_2",
                             "battery_power": "sensor.inverter_solarman_battery_power",
                             "grid": "sensor.inverter_solarman_grid_power"}.items():
            if self.hass.states.get(entity):
                roles[role] = entity
        if enabled and now.timestamp() >= self._pv_next_learning and all(roles.values()):
            self._pv_next_learning = now.timestamp() + 86400
            try:
                end = now.replace(hour=0, minute=0, second=0, microsecond=0)
                statistics = await get_instance(self.hass).async_add_executor_job(partial(
                    statistics_during_period, self.hass, dt_util.as_utc(end - timedelta(days=7)), dt_util.as_utc(end),
                    set(roles.values()), "5minute", None, {"mean"}))
                candidate = fit_pv(statistics, roles, now, nominal, layout_since)
                if candidate.get("accepted"):
                    self._pv_model = candidate
                    await self._pv_store.async_save(candidate)
                self._pv_info["learning_result"] = candidate.get("reason")
                self._pv_info["training_days"] = candidate.get("training_days", 0)
                self._pv_info["training_samples"] = candidate.get("training_samples", 0)
                self._pv_info["excluded_string_mismatch"] = candidate.get("excluded_string_mismatch", 0)
                self._pv_info["excluded_charge_limit"] = candidate.get("excluded_charge_limit", 0)
            except Exception as err:
                self._pv_next_learning = now.timestamp() + 3600
                self._pv_info["learning_result"] = type(err).__name__
                _LOGGER.warning("Local PV learning unavailable: %s", type(err).__name__)
        weather = self.hass.states.get(roles["irradiance"])
        current = super()._numeric_state(roles["actual"], power=True)
        forecast = super()._numeric_state(roles["forecast"], power=True)
        fresh = bool(weather and 0 <= now.timestamp() - getattr(weather, "last_reported", weather.last_updated).timestamp() <= 300
                     and weather.attributes.get("unit_of_measurement") in ("W/m²", "W/m2"))
        percent = self.hass.states.get("input_number.energy_manager_margine_stima_fv")
        maximum = self.hass.states.get("input_number.energy_manager_extra_massimo_stima_fv")
        margin = max(0, min(finite(percent.state if percent else None, 3), 10))
        extra_max = max(0, min(finite(maximum.state if maximum else None, 200), 500))
        local = estimate_pv(self._pv_model, weather.state, now, nominal, margin, extra_max) if fresh and enabled else None
        self._pv_estimate = local if local is not None else forecast
        self._pv_combined = combine_pv(current, self._pv_estimate)
        self._pv_info.update(method="local_irradiance_validated" if local is not None else "weather_forecast_fallback",
            actual_w=current, weather_forecast_w=forecast, hypothetical_w=self._pv_estimate,
            nominal_w=nominal, positive_margin_pct=margin, extra_max_w=extra_max, local_model_enabled=enabled,
            learning_sensors=roles, layout_since=layout_since, layout_date_valid=valid_layout, station_fresh=fresh, trained_at=self._pv_model.get("trained_at"),
            validation_samples=self._pv_model.get("validation_samples"),
            validation_mae_w=self._pv_model.get("validation_mae_w"),
            weather_mae_w=self._pv_model.get("weather_mae_w"))

    def _house_config(self):
        houses = [s for s in self.entry.subentries.values() if s.subentry_type == HOUSE_TYPE]
        rooms = [s for s in self.entry.subentries.values() if s.subentry_type == ROOM_TYPE]
        house = dict(houses[0].data) if houses else None
        if house is not None:
            for key, entity, default, lower, upper in (
                ("solar_storage_margin_c", "input_number.energy_manager_margine_accumulo_termico", 5, 0, 5),
                ("solar_storage_heat_max_c", "input_number.energy_manager_limite_caldo_accumulo", 27, 22, 27),
                ("solar_storage_cooling_margin_c", "input_number.energy_manager_margine_accumulo_fresco", 2, 0, 3),
                ("solar_storage_cool_min_c", "input_number.energy_manager_limite_freddo_accumulo", 24, 22, 26),
                ("solar_storage_threshold_ratio", "input_number.energy_manager_soglia_surplus_accumulo", 1.25, 1, 2)):
                state = self.hass.states.get(entity)
                house[key] = max(lower, min(finite(state.state if state else None, default), upper))
        return house, rooms

    def _house_claimed_entities(self):
        house, rooms = self._house_config()
        if not house or not house.get("enabled") or not house.get("reviewed"):
            return set()
        entities = {house.get("gas_entity", "")} if house.get("hydraulics_confirmed") else set()
        for room in rooms:
            if not room.data.get("reviewed"):
                continue
            if house.get("hydraulics_confirmed"):
                entities.update(room.data.get("radiator_entities") or [])
            entities.add(room.data.get("heat_pump_entity", ""))
        entities.discard("")
        return entities

    def _managed_device_snapshots(self):
        # Only an activated, reviewed house profile replaces legacy control.
        # Observation never changes existing device management.
        claimed = self._house_claimed_entities()
        return [r for r in super()._managed_device_snapshots()
                if not ({r.get("entity_id", ""), r.get("mode_climate_entity", "")} |
                        set(r.get("climate_group_entities") or [])) & claimed]

    def _room_temperature(self, room):
        sensor = room.get("temperature_entity")
        if sensor:
            state = self.hass.states.get(sensor)
            if state and state.state not in ("unknown", "unavailable"):
                value = finite(state.state)
                if state.attributes.get("unit_of_measurement") == "°F" and value is not None:
                    value = (value - 32) * 5 / 9
                elif state.attributes.get("unit_of_measurement") not in ("°C", "C"):
                    value = None
                if value is not None and -10 <= value <= 50:
                    return value
        values = []
        for entity in room.get("radiator_entities") or [room.get("heat_pump_entity", "")]:
            state = self.hass.states.get(entity)
            if state and state.state not in ("unknown", "unavailable"):
                value = finite(state.attributes.get("current_temperature"))
                if value is not None:
                    if self.hass.config.units.temperature_unit == "°F":
                        value = (value - 32) * 5 / 9
                    if -10 <= value <= 50:
                        values.append(value)
        if not values and room.get("radiator_entities") and room.get("heat_pump_entity"):
            fallback = self.hass.states.get(room["heat_pump_entity"])
            if fallback and fallback.state not in ("unknown", "unavailable"):
                value = finite(fallback.attributes.get("current_temperature"))
                if value is not None and self.hass.config.units.temperature_unit == "°F":
                    value = (value - 32) * 5 / 9
                if value is not None and -10 <= value <= 50:
                    values.append(value)
        return sum(values) / len(values) if values else None

    async def _house_save(self):
        await self._house_store.async_save({"reference_offsets": getattr(self, "_house_reference_offsets", {}), "owned": self._house_owned, "hold": self._house_hold,
                                            "gas_mode": self.house_gas_mode, "last_start": self._house_last_start,
                                            "machine_modes": self.house_machine_modes,
                                            "gas_stopped_at": self._house_gas_stopped_at,
                                            "history_until": getattr(self, "_house_history_until", 0),
                                            "exception": getattr(self, "_house_exception", {})})

    async def async_set_house_machine_mode(self, machine, mode):
        if machine not in self.house_machine_modes or mode not in ("auto", "manual", "off"):
            raise ValueError("Modalità climatizzatore non valida")
        if mode == "auto" and self.house_machine_modes.get(machine) != "auto":
            # Explicitly selecting Automatico authorizes ownership transfer.
            # A manual device change during automatic operation still releases
            # ownership and is never silently adopted.
            _, rooms = self._house_config()
            for room in rooms:
                if room.data.get("machine") != machine:
                    continue
                entity = room.data.get("heat_pump_entity", "")
                self._house_hold.pop(entity, None)
                state = self.hass.states.get(entity)
                target = finite(state.attributes.get("temperature")) if state else None
                if target is not None and self.hass.config.units.temperature_unit == "°F":
                    target = (target - 32) * 5 / 9
                if state and state.state in ("heat", "cool") and target is not None:
                    self._house_owned[entity] = {"mode": state.state, "target": target,
                        "fan": state.attributes.get("fan_mode"), "at": dt_util.now().timestamp(),
                        "mode_at": state.last_changed.timestamp()}
        if mode == "off":
            _, rooms = self._house_config()
            for room in rooms:
                if room.data.get("machine") == machine:
                    entity = room.data.get("heat_pump_entity", "")
                    state = self.hass.states.get(entity)
                    if self.real_control_enabled and state and "off" in state.attributes.get("hvac_modes", []):
                        await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity, "hvac_mode": "off"}, blocking=True)
                        self._house_owned.pop(entity, None)
        self.house_machine_modes[machine] = mode
        await self._house_save()
        await self.async_request_refresh()

    async def async_set_house_gas_mode(self, mode):
        if mode not in ("auto", "off"):
            raise ValueError("Modalità caldaia non valida")
        house, _ = self._house_config()
        if mode == "off":
            entity = house.get("gas_entity", "") if house else ""
            state = self.hass.states.get(entity)
            if not self.real_control_enabled or not entity.startswith("climate.") or not state or "off" not in state.attributes.get("hvac_modes", []):
                raise ValueError("Il termostato del riscaldamento non è disponibile o il controllo reale è disabilitato")
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity, "hvac_mode": "off"}, blocking=True)
            self._house_owned.pop(entity, None)
        elif mode == "manual" and house:
            self._house_owned.pop(house.get("gas_entity", ""), None)
        self.house_gas_mode = mode
        await self._house_save()
        await self.async_request_refresh()

    async def _house_command(self, entity, mode, target, now, compressor=False, safety_stop=False):
        state = self.hass.states.get(entity)
        if not entity.startswith("climate.") or not state or state.state in ("unknown", "unavailable"):
            return False
        if mode not in state.attributes.get("hvac_modes", []):
            return False
        owned = self._house_owned.get(entity)
        step = finite(state.attributes.get("target_temp_step"), .5)
        if self.hass.config.units.temperature_unit == "°F":
            step *= 5 / 9
        tolerance = max(step * .45, .05)
        if self._house_hold.get(entity, 0) > now.timestamp():
            return False
        actual = finite(state.attributes.get("temperature"))
        if self.hass.config.units.temperature_unit == "°F" and actual is not None:
            actual = (actual - 32) * 5 / 9
        # Never adopt a manually running compressor or the central thermostat.
        if not owned and compressor and state.state != "off":
            return False
        if owned and now.timestamp() - owned["at"] > 120 and (
            state.state != owned["mode"] or
            (owned.get("target") is not None and actual is not None and abs(actual - owned["target"]) > tolerance) or
            (owned.get("fan") is not None and now.timestamp() - owned.get("fan_at", owned["at"]) > 120
             and state.attributes.get("fan_mode") != owned["fan"])
        ):
            self._house_owned.pop(entity, None)
            self._house_hold[entity] = now.timestamp() + 2 * 3600
            await self._house_save()
            return False
        if mode == "off" and not owned:
            return False
        if state.state == mode and (mode == "off" or (actual is not None and abs(actual - target) < .3)):
            return False
        last_at = owned["at"] if owned else state.last_changed.timestamp()
        # Persisted mode_at is an absolute timestamp: downtime counts too.
        # Keep a running compressor on for 20 minutes, but only require
        # five minutes off before restarting. Heat/cool reversal retains
        # the longer running protection.
        minimum = (300 if state.state == "off" else 1200) if compressor and state.state != mode else 180
        if compressor and state.state != mode and owned:
            last_at = owned.get("mode_at", owned["at"])
        if safety_stop and mode == "off" and owned:
            minimum = 0
        if now.timestamp() - last_at < minimum:
            return False
        payload = {"entity_id": entity}
        if mode == "off":
            payload["hvac_mode"] = "off"
            service = "set_hvac_mode"
        else:
            lower = finite(state.attributes.get("min_temp"), 5)
            upper = finite(state.attributes.get("max_temp"), 30)
            if self.hass.config.units.temperature_unit == "°F":
                lower, upper = (lower - 32) * 5 / 9, (upper - 32) * 5 / 9
            target = max(min(target, upper), lower)
            if step > 0:
                target = max(lower, min(lower + round((target - lower) / step) * step, upper))
            payload.update(hvac_mode=mode, temperature=target * 9 / 5 + 32 if self.hass.config.units.temperature_unit == "°F" else target)
            service = "set_temperature"
        if compressor and mode != "off" and state.state != mode:
            # Midea mode writes update its cached power/mode. A combined
            # temperature write alone does not, so a following full-state
            # command can otherwise resend the old OFF state.
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity, "hvac_mode": mode}, blocking=True)
        await self.hass.services.async_call("climate", service, payload, blocking=True)
        self._house_owned[entity] = {**(owned or {}), "mode": mode, "target": target, "at": now.timestamp(),
                                     "mode_at": owned.get("mode_at", owned["at"]) if owned and owned["mode"] == mode else now.timestamp()}
        await self._house_save()
        return True

    async def _house_trv_reference(self, house, room, now):
        switch = self.hass.states.get("input_boolean.energy_manager_riferimento_testine_" + room.get("name", "").strip().lower().replace(" ", "_"))
        if not switch or switch.state != "on" or not room.get("radiator_entities"):
            return None, "native_or_learned"
        hp = room.get("heat_pump_entity", "")
        state = self.hass.states.get(hp)
        valves = [self.hass.states.get(e) for e in room.get("radiator_entities", [])]
        values = []
        for valve in valves:
            if not valve or valve.state in ("unknown", "unavailable"):
                continue
            at = getattr(valve, "last_reported", getattr(valve, "last_updated", valve.last_changed))
            value = celsius(valve.attributes.get("current_temperature"), self.hass.config.units.temperature_unit)
            if value is not None and 0 <= now.timestamp() - at.timestamp() <= 6 * 3600:
                values.append(value)
        native = celsius(state.attributes.get("current_temperature"), self.hass.config.units.temperature_unit) if state else None
        if not values or native is None:
            return None, "reference_unavailable_or_stale"
        reference = sum(values) / len(values)
        gas = self.hass.states.get(house.get("gas_entity", ""))
        stopped = getattr(self, "_house_gas_stopped_at", 0)
        hot = bool((gas and gas.state == "heat" and any(v and v.attributes.get("hvac_action") == "heating" for v in valves))
                   or stopped and 0 <= now.timestamp() - stopped < 3600)
        heating = bool(state and state.state in ("heat", "cool") and (finite(state.attributes.get("compressor_frequency"), 0) > 0
                        or finite(self._house_power_w(room, state), 0) > 100))
        saved = getattr(self, "_house_reference_offsets", {}).get(hp, {})
        delta = native - reference
        if not hot and not heating and abs(delta) <= 10:
            if not saved or abs(delta - saved.get("offset", 0)) >= .25 or now.timestamp() - saved.get("at", 0) >= 3600:
                self._house_reference_offsets = getattr(self, "_house_reference_offsets", {})
                self._house_reference_offsets[hp] = {"offset": delta, "at": now.timestamp()}
                await self._house_save()
            return delta, "trvs_radiators_cold"
        if saved and 0 <= now.timestamp() - saved.get("at", 0) <= 7 * 86400:
            return saved["offset"], "frozen_cold_offset_radiators_hot" if hot else "frozen_cold_offset_heat_pump_running"
        return None, "radiators_hot_no_valid_cold_offset" if hot else "no_valid_cold_offset"

    async def _house_storage_fan(self, entity, active, now):
        """Persist only our fan change; restore it without overwriting manual edits."""
        owned = self._house_owned.get(entity)
        state = self.hass.states.get(entity)
        if not owned or not state or state.state in ("unknown", "unavailable"):
            return False
        modes = state.attributes.get("fan_modes", [])
        actual = state.attributes.get("fan_mode")
        # Both raising AND restoring the fan serialize full Midea state.
        # Wait for every preceding mode/target command, including OFF.
        if (now.timestamp() - owned.get("at", now.timestamp()) < 60
                or state.state != owned.get("mode")):
            return False
        if owned.get("mode") in ("heat", "cool"):
            target = finite(state.attributes.get("temperature"))
            if self.hass.config.units.temperature_unit == "°F" and target is not None:
                target = (target - 32) * 5 / 9
            if target is None or owned.get("target") is None or abs(target - owned["target"]) > .3:
                return False
        if now.timestamp() - owned.get("fan_at", 0) < 60:
            return False
        if owned.get("fan") is not None and actual != owned["fan"]:
            return False
        if active and owned.get("mode") in ("heat", "cool") and "high" in modes:
            if "fan_previous" not in owned:
                if actual not in modes:
                    return False
                owned["fan_previous"] = actual
            wanted = "high"
        elif "fan_previous" in owned:
            wanted = owned["fan_previous"]
            if wanted not in modes:
                return False
        else:
            return False
        if actual != wanted:
            await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": entity, "fan_mode": wanted}, blocking=True)
            owned["fan_at"] = now.timestamp()
        owned["fan"] = wanted
        if not active:
            owned.pop("fan_previous", None)
        await self._house_save()
        return actual != wanted

    async def _async_house_plan(self, data, now):
        house, rooms = self._house_config()
        if not house:
            data.update(house_climate_status="unconfigured", house_climate_rooms=[])
            return
        task = getattr(self, "_house_history_task", None)
        history_state = getattr(self, "_house_history_status", "pending")
        if house.get("engine_url") and house.get("outdoor_entity") and (not task or task.done()) and (history_state == "pending" or history_state.startswith("retry_needed")) and now.timestamp() >= getattr(self, "_house_history_retry_at", 0):
            self._house_history_retry_at = now.timestamp() + 900
            self._house_history_task = self.hass.async_create_background_task(self._import_climate_history(), "Energy Meter climate history retry")
        house["pv_entity"] = self._config("pv_power_sensor")
        house["timezone"] = getattr(self.hass.config, "time_zone", "UTC")
        house["exception"] = getattr(self, "_house_exception", {})
        guest = self.hass.states.get((self._config("house_guest_entity") or "input_boolean.modalita_ospite"))
        if guest and guest.state == "on":
            if self._house_exception:
                self._house_exception = {}
                await self._house_save()
            house["exception"] = {"mode": "guest", "persistent": True}
        if situation(house, now) == "normal" and house["exception"]:
            self._house_exception = {}
            house["exception"] = {}
            await self._house_save()
        rooms = sorted(rooms, key=lambda r: -finite(r.data.get("priority"), 2))
        meter = self.hass.states.get(house.get("gas_meter_entity", ""))
        house["gas_meter_reading"] = finite(meter.state) if meter else None
        outside = self.hass.states.get(house.get("outdoor_entity", ""))
        house["outdoor_temperature"] = celsius(outside.state, outside.attributes.get("unit_of_measurement"), -40) if outside else None
        house["gas_mode"] = self.house_gas_mode
        house["machine_modes"] = self.house_machine_modes
        if house.get("weather_entity") and now.timestamp() - getattr(self, "_house_weather_last_at", 0) >= 3600:
            self._house_weather_last_at = now.timestamp()
            try:
                result = await self.hass.services.async_call("weather", "get_forecasts", {"entity_id": house["weather_entity"], "type": "hourly"}, blocking=True, return_response=True)
                weather = self.hass.states.get(house["weather_entity"])
                unit = weather.attributes.get("temperature_unit", self.hass.config.units.temperature_unit) if weather else self.hass.config.units.temperature_unit
                self._house_weather_forecast = [{**point, "temperature": celsius(point.get("temperature"), unit, -40)} for point in (result or {}).get(house["weather_entity"], {}).get("forecast", [])[:48]]
            except Exception:
                self._house_weather_forecast = []
        ready = bool(house.get("reviewed") and rooms and any(s.data.get("reviewed") for s in rooms))
        execute = bool(house.get("enabled") and ready and self.real_control_enabled)
        emergency = await self._house_power_guard(house, rooms, data, now) if execute else False
        data["house_climate_status"] = "automatic" if execute else "observation"
        data["house_climate_profile_ready"] = ready
        data["house_climate_gas_mode"] = self.house_gas_mode
        data["house_climate_error"] = self._house_error
        data["house_climate_history_status"] = getattr(self, "_house_history_status", "pending")
        data["house_climate_exception"] = house["exception"]
        zones = []
        allocation_live = data.get("v156_battery_allocation") or {}
        live_surplus = self._thermal_solar_available(data)
        for s in rooms:
            r = dict(s.data)
            hp = self.hass.states.get(r.get("heat_pump_entity", ""))
            valves = [self.hass.states.get(e) for e in r.get("radiator_entities", [])]
            measured_power = self._house_power_w(r, hp)
            hp_heat = bool(hp and hp.state == "heat" and (hp.attributes.get("hvac_action") == "heating" or finite(measured_power, 0) > 100 or finite(hp.attributes.get("compressor_frequency"), 0) > 0))
            gas_state = self.hass.states.get(house.get("gas_entity", ""))
            gas_heat = bool(gas_state and gas_state.attributes.get("hvac_action") == "heating" and any(v and v.attributes.get("hvac_action") == "heating" for v in valves))
            thermal_source = "combined" if hp_heat and gas_heat else "heat_pump" if hp_heat else "gas" if gas_heat else "off"
            contamination = []
            machine = r.get("machine", "none")
            if hp and hp.state not in ("off", "unknown", "unavailable") and self.house_machine_modes.get(machine) != "auto":
                contamination.append("manual_heat_pump")
            window = self.hass.states.get(r.get("window_entity", ""))
            if r.get("window_entity") and (not window or window.state != "off"):
                contamination.append("window_open_or_unknown")
            sensors = self._room_sensor_values(r)
            outdoor = self.hass.states.get(house.get("outdoor_entity", ""))
            for entity in r.get("contamination_entities", []):
                extra = self.hass.states.get(entity)
                if not extra or auxiliary_active(extra.state, extra.attributes):
                    contamination.append("auxiliary_source_or_unknown")
            if house["exception"]:
                contamination.append("temporary_house_exception")
            for entity in r.get("window_entities", []):
                w = self.hass.states.get(entity)
                if not w or w.state != "off":
                    contamination.append("window_open_or_unknown")
            r["dispatch_mode"] = "cool" if house.get("season") == "summer" else "heat"
            mean_w, start_w, _ = self._house_group_power_estimate(r, rooms, now)
            r["dispatch_group_power_w"] = start_w
            r["solar_storage_available"] = live_surplus >= start_w * finite(house.get("solar_storage_threshold_ratio"), 1.25)
            zones.append({"id": s.subentry_id, "config": r, "temperature": self._room_temperature(r),
                          "thermal_source": thermal_source, "contamination": contamination,
                          "sensors": sensors, "independent_temperature": sensors.get(r.get("temperature_entity")),
                          "outdoor_temperature": celsius(outdoor.state, outdoor.attributes.get("unit_of_measurement"), -40) if outdoor else None,
                          "power_w": measured_power,
                          "solar_power_w": finite(data.get("pv_power_w")),
                          "neighbor_gradients": {e: temp - self._room_temperature(r) for e in r.get("neighbor_temperature_entities", []) if self._room_temperature(r) is not None and (temp := self._room_sensor_values({"temperature_entity": e} if e.startswith("sensor.") else {"heat_pump_entity": e}).get(e)) is not None},
                          "solar_available": live_surplus >= start_w * .95,
                          "electrical_ok": self.house_machine_modes.get(machine) == "auto" and not data.get("grid_warning") and not data.get("inverter_warning") and r.get("phase") in ("l1", "l2", "l3") and finite(data.get("phase_" + r.get("phase", "unknown") + "_headroom_w"), 0) >= start_w})
        if now.timestamp() - getattr(self, "_house_engine_last_at", 0) >= 60:
            self._house_engine_result = await async_plan(self.hass, house, zones, {
                "pv_remaining_kwh": data.get("forecast_energy_to_target_kwh"),
                "battery_input_need_kwh": data.get("battery_input_energy_needed_kwh"),
                "house_remaining_kwh": data.get("base_load_energy_to_target_kwh"),
                "battery_capacity_kwh": self._config("battery_capacity_kwh"),
                "hvac_power_limit_w": max(finite(data.get("inverter_headroom_w"), 0), 0),
                "outdoor_forecast": getattr(self, "_house_weather_forecast", []),
                "soc": data.get("battery_soc"), "target_soc": (data.get("v156_battery_allocation") or {}).get("target_soc")}, now)
            self._house_engine_last_at = now.timestamp()
        data["engine_state"] = self._house_engine_result.get("state", "DEGRADED")
        if emergency:
            data["engine_state"] = "SAFE"
        data["engine_reasons"] = self._house_engine_result.get("reasons", [])
        data["engine_recent_decisions"] = self._house_engine_result.get("recent_decisions", [])
        data["engine_energy_budget"] = self._house_engine_result.get("energy_budget", {})
        data["engine_horizon"] = self._house_engine_result.get("horizon", {})
        def compact(value):
            return {key: compact(item) if isinstance(item, dict) else item for key, item in value.items() if not isinstance(item, list)}
        data["engine_zone_models"] = {key: compact(zone.get("model", {})) for key, zone in self._house_engine_result.get("zones", {}).items()}
        data["engine_history_imported_until"] = self._house_engine_result.get("history_imported_until")
        data["engine_machine_models"] = self._house_engine_result.get("machine_models", {})
        data["engine_gas_campaign"] = self._house_engine_result.get("gas_campaign", {})
        decisions = []
        available = self._thermal_solar_available(data)
        allocation = data.get("v156_battery_allocation") or {}
        grid_helper = self.hass.states.get("input_number.energy_manager_supporto_rete_clima")
        grid_support = max(0, min(finite(grid_helper.state if grid_helper else None, 0), 500))
        startup_grace = any(entity.startswith("climate.") and owner.get("mode") in ("heat", "cool")
                            and 0 <= now.timestamp() - owner.get("mode_at", owner.get("at", 0)) < 180
                            for entity, owner in self._house_owned.items())
        grid_peak_limit = grid_support * (2 if startup_grace else 1)
        if (finite(data.get("grid_import_w"), 0) <= grid_peak_limit
                and finite(data.get("battery_discharge_w"), 0) <= 100
                and not data.get("grid_warning") and not data.get("inverter_warning")
                and finite(data.get("battery_soc"), 0) >= 97
                and (data.get("planner_policy") or {}).get("target_reachability") != "definite_shortfall"):
            potential = max(finite(data.get("pv_potential_after_house_w"), 0), 0)
            if allocation.get("below_target"):
                potential = min(potential, max(finite(allocation.get("overflow_w"), 0), 0))
            available = max(available, potential)
        phase_budget = {p: finite(data.get(f"phase_{p}_headroom_w"), 0) for p in ("l1", "l2", "l3")}
        group_reserved = {}
        cycle_started_groups = set()
        inverter_budget = finite(data.get("inverter_headroom_w"), 0)
        gas_demand = False
        gas_sensors_valid = False
        # Start no second electrical load before telemetry reflects a boiler action.
        last_thermal = dt_util.parse_datetime(self._last_thermal_at) if self._last_thermal_at else None
        settling = bool(last_thermal and 0 <= now.timestamp() - last_thermal.timestamp() < 60)
        for subentry in rooms:
            room = dict(subentry.data)
            learned = self._house_engine_result.get("zones", {}).get(subentry.subentry_id, {})
            if learned.get("learning_status") in ("thermal_rate_available", "conservative_fallback"):
                room["preheat_minutes"] = max(15, min(finite(learned.get("preheat_minutes"), 90), 480))
            elif not learned:
                # An unavailable Engine must not move an already planned
                # recovery back to a short uncalibrated default. Local control
                # anticipates conservatively; targets, surplus/battery budget,
                # phase guards and manual ownership still gate every command.
                room["preheat_minutes"] = 480
            current = self._room_temperature(room)
            estimate = learned.get("estimated_temperature")
            if learned.get("estimate_quality") == "calibrated_fusion" and finite(estimate) is not None and current is not None and abs(estimate - current) <= 3:
                current = estimate
            hp = room.get("heat_pump_entity", "")
            hp_state = self.hass.states.get(hp)
            machine = room.get("machine", "none")
            room["dispatch_mode"] = "cool" if house.get("season") == "summer" else "heat"
            group_key = room.get("power_entity") or machine
            average_w, start_w, power_basis = self._house_group_power_estimate(room, rooms, now)
            phase = room.get("phase", "unknown")
            is_owned = hp in self._house_owned and hp_state and hp_state.state != "off"
            real_watts = finite(self._house_power_w(room, hp_state), 0)
            group_reserved.setdefault(group_key, real_watts)
            watts = max(start_w - group_reserved[group_key], 0)
            covered = 0
            support_left = max(grid_support - max(finite(data.get("grid_import_w"), 0), 0), 0)
            electrical = (not data.get("grid_warning") and not data.get("inverter_warning") and phase in phase_budget
                          and phase_budget[phase] >= watts
                          and inverter_budget >= watts and (is_owned or not settling))
            if (not is_owned and now.timestamp() - self._house_last_start < 120
                    and not (machine == "p1" and group_key in cycle_started_groups)):
                electrical = False
            average_increment = max(average_w - group_reserved[group_key], 0)
            solar = (available + support_left >= average_increment
                     and (available > 100 or group_reserved[group_key] > 100)
                     and finite(data.get("grid_import_w"), 0) <= grid_peak_limit
                     and finite(data.get("battery_discharge_w"), 0) <= 100)
            if not solar and (allocation.get("below_target") or finite(data.get("grid_headroom_w"), 0) < (0 if is_owned else watts)):
                electrical = False
            machine = room.get("machine", "none")
            machine_mode = self.house_machine_modes.get(machine, "manual")
            if machine_mode != "auto":
                electrical = False
            # A manually selected mode on any P1 head locks out incompatible starts.
            wanted = "cool" if house.get("season") == "summer" else "heat"
            incompatible = any(r.data.get("machine") == machine and (s := self.hass.states.get(r.data.get("heat_pump_entity", "")))
                               and s.state in ("heat", "cool", "heat_cool", "auto") and s.state != wanted for r in rooms)
            if incompatible:
                electrical = False
            room["solar_storage_available"] = solar and available + support_left >= average_increment * finite(house.get("solar_storage_threshold_ratio"), 1.25)
            decision = room_plan(house, room, now, current, solar, electrical)
            decision["subentry_id"] = subentry.subentry_id
            decision["commands_enabled"] = execute and room.get("reviewed", False)
            decision["confidence"] = learned.get("confidence", 0)
            decision["preheat_minutes"] = room.get("preheat_minutes", 90)
            decision.update(shared_power_sensor=room.get("power_entity"), group_average_power_w=round(average_w, 1),
                            group_start_budget_w=round(start_w, 1), incremental_power_w=round(watts, 1),
                            power_estimate_basis=power_basis, solar_available_w=round(available, 1), grid_support_w=grid_support, grid_peak_limit_w=grid_peak_limit, measured_group_power_w=round(real_watts, 1), compressor_frequency_hz=finite(hp_state.attributes.get("compressor_frequency")) if hp_state else None)
            decision["estimated_temperature"] = current
            decision["overshoot_c"] = max(0, min(finite(learned.get("overshoot_c"), 0), 1))
            if decision["heat_pump_mode"] == "heat" and current is not None and current >= finite(decision["heat_pump_target"], decision["room_target"]) - decision["overshoot_c"]:
                decision.update(heat_pump_mode="off", source="learned_inertia_coast")
            if emergency:
                decision.update(source="power_guard", heat_pump_mode="off")
            window = self.hass.states.get(room.get("window_entity", ""))
            if (room.get("window_entity") and (not window or window.state != "off")) or any(not (w := self.hass.states.get(e)) or w.state != "off" for e in room.get("window_entities", [])):
                decision.update(source="window_open_or_unknown", radiator_target=5, heat_pump_mode="off")
            if hp_state and hp_state.state not in ("off", "unknown", "unavailable") and not is_owned:
                decision.update(source="manual_heat_pump", radiator_target=finite(room.get("maintenance_temperature"), 17) if house.get("season") == "winter" and hp_state.state == "heat" else None, heat_pump_mode="off")
            if current is not None and room.get("radiator_entities") and decision["radiator_target"] is not None and house.get("season") == "winter":
                if decision["source"] not in ("window_open_or_unknown", "profile_to_confirm"):
                    gas_sensors_valid = True
                    gas_demand |= current < decision["radiator_target"] - .3
            room_heat_limit = min(min(finite(room.get("comfort_temperature"), 21), 22) + finite(house.get("solar_storage_margin_c"), 5), finite(house.get("solar_storage_heat_max_c"), 27))
            room_temperature_guard = bool(current is not None and hp_state and
                ((hp_state.state == "heat" and current >= room_heat_limit) or
                 (hp_state.state == "cool" and current <= finite(house.get("solar_storage_cool_min_c"), 24))))
            decision["room_temperature_limit_reached"] = room_temperature_guard
            decisions.append(decision)
            if not execute or not room.get("reviewed"):
                continue
            if decision["heat_pump_mode"] != "off":
                mode = decision["heat_pump_mode"]
                if hp_state and mode in hp_state.attributes.get("hvac_modes", []):
                    reference_offset, reference_quality = await self._house_trv_reference(house, room, now)
                    decision.update(temperature_reference_quality=reference_quality, native_reference_offset_c=reference_offset)
                    target = self._house_calibrated_setpoint(hp, decision["heat_pump_target"], "cooling" if mode == "cool" else "heat_pump", learned, mode,
                        finite(house.get("solar_storage_heat_max_c"), 27) if decision.get("solar_storage_active") else 22,
                        finite(house.get("solar_storage_cool_min_c"), 24) if decision.get("solar_storage_active") else 20, reference_offset=reference_offset)
                    decision["device_setpoint"] = target
                    started = await self._house_command(hp, mode, target, now, compressor=True)
                    fan_option = self.hass.states.get("input_boolean.energy_manager_ventola_alta_surplus")
                    fan_active = bool(fan_option and fan_option.state == "on" and decision.get("solar_storage_active"))
                    await self._house_storage_fan(hp, fan_active, now)
                    if started and not is_owned:
                        cycle_started_groups.add(group_key)
                        self._house_last_start = now.timestamp()
                        await self._house_save()
                    if started or is_owned:
                        # Reserve only the increment of this shared external unit.
                        # Failed starts never consume the following room's budget.
                        used_solar = min(available, watts)
                        available = max(available - watts, 0)
                        grid_support = max(grid_support - max(watts - used_solar, 0), 0)
                        group_reserved[group_key] = max(group_reserved[group_key], start_w)
                        phase_budget[phase] -= watts
                        inverter_budget -= watts
            elif is_owned and machine_mode == "auto":
                # Surplus, room targets, windows and admission budgets are
                # ordinary scheduling decisions, not electrical emergencies.
                # They must never bypass the persisted 20-minute ON dwell.
                await self._house_command(hp, "off", None, now, compressor=True,
                                          safety_stop=emergency)
            if decision["heat_pump_mode"] == "off" and machine_mode == "auto":
                await self._house_storage_fan(hp, False, now)
            if (house.get("hydraulics_confirmed") and self.house_gas_mode == "auto" and decision["radiator_target"] is not None and house.get("season") == "winter"
                    and now.timestamp() - self._house_gas_stopped_at >= finite(house.get("dissipation_seconds"), 180)):
                gas_entity = house.get("gas_entity", "")
                gas_state = self.hass.states.get(gas_entity)
                lowering = any((s := self.hass.states.get(e)) and finite(s.attributes.get("temperature"), 0) > decision["radiator_target"] + .3
                               for e in room.get("radiator_entities") or [])
                if lowering and gas_state and gas_state.state == "heat" and gas_entity in self._house_owned:
                    if await self._house_command(gas_entity, "off", None, now, compressor=True, safety_stop=True):
                        self._house_gas_stopped_at = now.timestamp()
                        await self._house_save()
                    continue
                for valve in room.get("radiator_entities") or []:
                    target = self._house_calibrated_setpoint(valve, decision["radiator_target"], "gas", learned, "heat")
                    await self._house_command(valve, "heat", target, now)
        data["house_climate_rooms"] = decisions
        self._house_last_decisions = decisions
        demanding_rooms = set()
        demanding_valves = set()
        if execute:
            # Wait until a real valve acknowledges an open heat demand. A desired
            # setpoint alone must not fire the boiler against closed valves.
            gas_demand = False
            for subentry, decision in zip(rooms, decisions):
                current = decision["current_temperature"]
                if current is None or decision["radiator_target"] is None or decision["source"] == "window_open_or_unknown":
                    continue
                for entity in subentry.data.get("radiator_entities") or []:
                    valve = self.hass.states.get(entity)
                    target = finite(valve.attributes.get("temperature")) if valve else None
                    if target is not None and self.hass.config.units.temperature_unit == "°F":
                        target = (target - 32) * 5 / 9
                    valve_current = finite(valve.attributes.get("current_temperature")) if valve else None
                    if valve_current is not None and self.hass.config.units.temperature_unit == "°F":
                        valve_current = (valve_current - 32) * 5 / 9
                    action = valve.attributes.get("hvac_action") if valve else None
                    verified = bool(valve and valve.state == "heat" and target is not None and valve_current is not None
                                    and valve_current < target - .3 and action == "heating")
                    if not verified:
                        self._house_open_since.pop(entity, None)
                        continue
                    self._house_open_since.setdefault(entity, now.timestamp())
                    if now.timestamp() - self._house_open_since[entity] >= finite(house.get("valve_open_seconds"), 180):
                        demanding_rooms.add(subentry.subentry_id)
                        demanding_valves.add(entity)
            # Require two distinct physical valves; they may be in the same room.
            gas_demand = bool(house.get("hydraulics_confirmed") and len(demanding_valves) >= 2
                              and now.timestamp() - self._house_gas_stopped_at >= finite(house.get("dissipation_seconds"), 180))
        data["house_climate_gas_demand_room_count"] = len(demanding_rooms)
        data["house_climate_gas_demand_valve_count"] = len(demanding_valves)
        data["house_climate_gas_demand"] = gas_demand
        if execute and house.get("hydraulics_confirmed") and self.house_gas_mode == "auto" and house.get("gas_entity"):
            gas = self.hass.states.get(house["gas_entity"])
            if house.get("season") == "winter" and not gas_demand:
                stopped = await self._house_command(house["gas_entity"], "off", None, now, compressor=True, safety_stop=True)
                if stopped:
                    self._house_gas_stopped_at = now.timestamp()
                    await self._house_save()
            elif house.get("season") == "winter" and gas_sensors_valid:
                if gas_demand and gas:
                    current = finite(gas.attributes.get("current_temperature"))
                    if current is not None:
                        if self.hass.config.units.temperature_unit == "°F":
                            current = (current - 32) * 5 / 9
                        target = min(max(finite(house.get("gas_call_temperature"), 26), current + .5), 30)
                        await self._house_command(house["gas_entity"], "heat", target, now, compressor=True)
                elif not gas_demand:
                    await self._house_command(house["gas_entity"], "off", None, now, compressor=True, safety_stop=True)
            elif house.get("season") != "winter":
                await self._house_command(house["gas_entity"], "off", None, now, compressor=True, safety_stop=True)


    def _house_group_power_estimate(self, room, rooms, now):
        """One external-unit estimate, never nominal power times head count."""
        machine = room.get("machine", "none")
        members = [r for r in rooms if r.data.get("machine") == machine and r.data.get("heat_pump_entity")]
        nominal = max([finite(r.data.get("nominal_power_w"), 1200) for r in members] or [1200])
        active = sum(bool((state := self.hass.states.get(r.data.get("heat_pump_entity", "")))
                          and state.state in ("heat", "cool", "auto", "heat_cool", "dry")) for r in members)
        own_state = self.hass.states.get(room.get("heat_pump_entity", ""))
        wanted_heads = max(1, active + (0 if own_state and own_state.state != "off" else 1))
        buckets = self._house_engine_result.get("machine_models", {}).get(machine, {})
        estimates = []
        for count, bucket in buckets.items():
            try:
                if ":" in str(count):
                    mode, count = str(count).rsplit(":", 1)
                    wanted_mode = "cool" if room.get("dispatch_mode") == "cool" else "heat"
                    if mode != wanted_mode:
                        continue
                count = int(count)
            except (ValueError, TypeError):
                continue
            powers = [finite(v) for v in bucket.get("powers", [])]
            powers = [v for v in powers if v is not None and max(100, nominal * .05) <= v <= nominal * 1.2]
            age = now.timestamp() - finite(bucket.get("updated_at"), 0)
            if count >= wanted_heads and len(powers) >= 12 and 0 <= age <= 7 * 86400:
                average = sum(powers) / len(powers)
                estimates.append((average, min(nominal, average * 1.35 + 150)))
        if estimates:
            average, start = max(estimates, key=lambda pair: pair[1])
            return average, start, "recent_group_mean_with_start_margin"
        return nominal, nominal, "group_nominal_fallback"

    def _house_power_w(self, room, hp):
        entity = room.get("power_entity", "")
        state = self.hass.states.get(entity)
        if state and state.state not in ("unknown", "unavailable"):
            value = finite(state.state)
            unit = state.attributes.get("unit_of_measurement")
            if value is not None and unit in ("W", "kW"):
                return max(value * (1000 if unit == "kW" else 1), 0)
        # Unlabelled device attributes are not a power measurement. Compressor
        # action/frequency can identify activity, not watts or COP.
        return None

    def _room_sensor_values(self, room):
        result = {}
        for entity in [room.get("temperature_entity", ""), *(room.get("radiator_entities") or []), room.get("heat_pump_entity", "")]:
            state = self.hass.states.get(entity)
            if not state or state.state in ("unknown", "unavailable"):
                continue
            value = celsius(state.attributes.get("current_temperature"), self.hass.config.units.temperature_unit) if entity.startswith("climate.") else celsius(state.state, state.attributes.get("unit_of_measurement"))
            if value is not None:
                result[entity] = value
        return result

    def _house_calibrated_setpoint(self, entity, target, source, learned, mode, heat_max=22, cool_min=20, reference_offset=None):
        if reference_offset is not None:
            target += reference_offset
            state = self.hass.states.get(entity)
            lower = celsius(state.attributes.get("min_temp", 16), self.hass.config.units.temperature_unit) if state else 16
            upper = celsius(state.attributes.get("max_temp", 30), self.hass.config.units.temperature_unit) if state else 30
            if source == "heat_pump" and mode == "heat":
                upper = min(upper if upper is not None else 30, 28)
            return max(lower if lower is not None else 16, min(target, upper if upper is not None else 30))
        bias = learned.get("model", {}).get("sensor_bias", {}).get(entity + ":" + source, {})
        if bias.get("samples", 0) >= 12 and finite(bias.get("confidence"), 0) >= .3:
            target += max(-2, min(finite(bias.get("offset_c"), 0), 2))
        return min(target, heat_max, 28) if mode == "heat" and source == "heat_pump" else min(target, heat_max) if mode == "heat" else max(cool_min, min(target, 30))

    async def _house_power_guard(self, house, rooms, data, now):
        """Hardware emergency also applies to manual loads, never to the Ariston main switch."""
        grid_limit = finite(data.get("grid_power_limit_w"), 6000)
        inverter_limit = finite(data.get("inverter_power_limit_w"), 8000)
        phase_limit = finite(data.get("phase_power_limit_w"), 3300)
        over_grid = finite(data.get("grid_import_w"), 0) > grid_limit + 100
        if over_grid:
            self._house_grid_over_since = getattr(self, "_house_grid_over_since", None) or now.timestamp()
        else:
            self._house_grid_over_since = None
        sustained = over_grid and now.timestamp() - self._house_grid_over_since >= 15
        inverter = finite(data.get("load_power_w"), 0) >= inverter_limit
        phases = {p for p in ("l1", "l2", "l3") if finite(data.get(f"phase_{p}_power_w"), 0) >= phase_limit}
        critical = bool(sustained or inverter or phases)
        if not critical or now.timestamp() - getattr(self, "_house_last_shed", 0) < 15:
            return critical
        for room in sorted(rooms, key=lambda r: finite(r.data.get("priority"), 2)):
            entity = room.data.get("heat_pump_entity", "")
            state = self.hass.states.get(entity)
            if not state or state.state not in ("heat", "cool", "auto", "heat_cool", "dry") or "off" not in state.attributes.get("hvac_modes", []):
                continue
            if phases and not (sustained or inverter) and room.data.get("phase") not in phases:
                continue
            await self.hass.services.async_call("climate", "set_hvac_mode", {"entity_id": entity, "hvac_mode": "off"}, blocking=True)
            self._house_owned.pop(entity, None)
            self._house_hold[entity] = now.timestamp() + 1200
            self._house_last_shed = now.timestamp()
            await self._house_save()
            break
        return critical

    async def async_set_house_exception(self, mode, hours=12):
        if mode not in ("normal", "guest", "holiday"):
            raise ValueError("Invalid house situation")
        entity = (self._config("house_guest_entity") or "input_boolean.modalita_ospite")
        state = self.hass.states.get(entity)
        if mode == "guest" and (not state or state.state in ("unknown", "unavailable")):
            raise ValueError("Modalità ospiti non disponibile")
        if state and state.state not in ("unknown", "unavailable"):
            await self.hass.services.async_call("input_boolean", "turn_on" if mode == "guest" else "turn_off", {"entity_id": entity}, blocking=True)
        self._house_exception = {"mode": "holiday", "persistent": True} if mode == "holiday" else {}
        await self._house_save()
        await self.async_request_refresh()

    async def _import_climate_history(self):
        house, rooms = self._house_config()
        if not house or not rooms or not house.get("engine_url") or not house.get("outdoor_entity"):
            return
        end = dt_util.as_utc(dt_util.now()).replace(minute=0, second=0, microsecond=0)
        start = max(end - timedelta(days=10), dt_util.utc_from_timestamp(self._house_history_until)) if self._house_history_until else end - timedelta(days=10)
        house["pv_entity"] = self._config("pv_power_sensor")
        entity_ids = {house.get("gas_entity", ""), house.get("outdoor_entity", ""), house.get("pv_entity", "")}
        configs = {r.subentry_id: dict(r.data) for r in rooms}
        for room in configs.values():
            entity_ids.update([room.get("heat_pump_entity", ""), room.get("temperature_entity", ""), *(room.get("radiator_entities") or []), *(room.get("window_entities") or []), *(room.get("contamination_entities") or []), *(room.get("neighbor_temperature_entities") or []), room.get("power_entity", "")])
        entity_ids.discard("")
        self._house_history_status = "importing"
        try:
            while start < end:
                stop = min(start + timedelta(hours=24), end)
                states = await get_instance(self.hass).async_add_executor_job(partial(get_significant_states, self.hass, start, stop, list(entity_ids), include_start_time_state=True, significant_changes_only=False, minimal_response=False, no_attributes=False))
                rows = {e: [{"last_updated": s.last_updated, "state": s.state, "attributes": dict(s.attributes)} for s in values] for e, values in states.items()}
                if not any(rows.get(r.get("temperature_entity")) or rows.get(r.get("heat_pump_entity")) or any(rows.get(e) for e in r.get("radiator_entities", [])) for r in configs.values()):
                    self._house_history_status = "history_empty"
                else:
                    points = await self.hass.async_add_executor_job(replay, rows, configs, house, start, stop, self.hass.config.units.temperature_unit)
                    for offset in range(0, len(points), 24):
                        if not await async_import_history(self.hass, house, points[offset:offset+24]):
                            raise RuntimeError("Engine history import unavailable")
                self._house_history_until = stop.timestamp()
                await self._house_save()
                start = stop
            if self._house_history_status != "history_empty":
                self._house_history_status = "ready"
        except Exception as err:
            self._house_history_status = "retry_needed:" + type(err).__name__
            _LOGGER.warning("Climate history import unavailable: %s", type(err).__name__)

    async def async_prepare_unload(self):
        if self._house_history_task and not self._house_history_task.done():
            self._house_history_task.cancel()
        await super().async_prepare_unload()

    async def _async_update_data(self):
        await self._refresh_pv_nowcast(dt_util.now())
        data = await super()._async_update_data()
        data["pv_local_nowcast_w"] = self._pv_estimate
        data["pv_nowcast_details"] = self._pv_info
        try:
            await self._async_house_plan(data, dt_util.now())
            self._house_error = None
        except Exception as err:
            self._house_error = type(err).__name__
            data["house_climate_error"] = self._house_error
            _LOGGER.warning("House climate planning failed: %s", type(err).__name__)
        return data

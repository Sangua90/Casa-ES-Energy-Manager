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

_LOGGER = logging.getLogger(__name__)


class CasaESEnergyCoordinator(PreviousCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, entry)
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
        saved = await self._house_store.async_load()
        if isinstance(saved, dict):
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

    def _house_config(self):
        houses = [s for s in self.entry.subentries.values() if s.subentry_type == HOUSE_TYPE]
        rooms = [s for s in self.entry.subentries.values() if s.subentry_type == ROOM_TYPE]
        return (dict(houses[0].data) if houses else None), rooms

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
        await self._house_store.async_save({"owned": self._house_owned, "hold": self._house_hold,
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
            (owned.get("fan") is not None and state.attributes.get("fan_mode") != owned["fan"])
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
        minimum = 1200 if compressor and state.state != mode else 180
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
        await self.hass.services.async_call("climate", service, payload, blocking=True)
        self._house_owned[entity] = {"mode": mode, "target": target, "at": now.timestamp(),
                                     "mode_at": owned.get("mode_at", owned["at"]) if owned and owned["mode"] == mode else now.timestamp()}
        await self._house_save()
        return True

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
        if situation(house, now) == "normal" and house["exception"]:
            self._house_exception = {}
            house["exception"] = {}
            await self._house_save()
        rooms = sorted(rooms, key=lambda r: -finite(r.data.get("priority"), 2))
        meter = self.hass.states.get(house.get("gas_meter_entity", ""))
        house["gas_meter_reading"] = finite(meter.state) if meter else None
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
        live_surplus = max(finite(data.get("grid_export_w"), 0), 0)
        if finite(data.get("battery_discharge_w"), 0) <= 100 and self._virtual_surplus_opportunity(data):
            live_surplus = max(live_surplus, max(finite(data.get("pv_potential_after_house_w"), 0), 0))
        if allocation_live.get("below_target"):
            live_surplus = min(live_surplus, max(finite(allocation_live.get("overflow_w"), 0), 0))
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
            zones.append({"id": s.subentry_id, "config": r, "temperature": self._room_temperature(r),
                          "thermal_source": thermal_source, "contamination": contamination,
                          "sensors": sensors, "independent_temperature": sensors.get(r.get("temperature_entity")),
                          "outdoor_temperature": celsius(outdoor.state, outdoor.attributes.get("unit_of_measurement"), -40) if outdoor else None,
                          "power_w": measured_power,
                          "solar_power_w": finite(data.get("pv_power_w")),
                          "neighbor_gradients": {e: temp - self._room_temperature(r) for e in r.get("neighbor_temperature_entities", []) if self._room_temperature(r) is not None and (temp := self._room_sensor_values({"temperature_entity": e} if e.startswith("sensor.") else {"heat_pump_entity": e}).get(e)) is not None},
                          "solar_available": live_surplus >= finite(r.get("nominal_power_w"), 1200) * .95,
                          "electrical_ok": self.house_machine_modes.get(machine) == "auto" and not data.get("grid_warning") and not data.get("inverter_warning") and r.get("phase") in ("l1", "l2", "l3") and finite(data.get("phase_" + r.get("phase", "unknown") + "_headroom_w"), 0) >= finite(r.get("nominal_power_w"), 1200)})
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
        available = max(finite(data.get("grid_export_w"), 0), 0)
        allocation = data.get("v156_battery_allocation") or {}
        if allocation.get("below_target"):
            available = min(available, max(finite(allocation.get("overflow_w"), 0), 0))
        if finite(data.get("battery_discharge_w"), 0) <= 100 and self._virtual_surplus_opportunity(data):
            potential = max(finite(data.get("pv_potential_after_house_w"), 0), 0)
            if allocation.get("below_target"):
                potential = min(potential, max(finite(allocation.get("overflow_w"), 0), 0))
            available = max(available, potential)
        phase_budget = {p: finite(data.get(f"phase_{p}_headroom_w"), 0) for p in ("l1", "l2", "l3")}
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
            watts = finite(room.get("nominal_power_w"), 1200)
            phase = room.get("phase", "unknown")
            is_owned = hp in self._house_owned and hp_state and hp_state.state != "off"
            real_watts = finite(self._house_power_w(room, hp_state), 0)
            covered = max(real_watts - finite(data.get("grid_import_w"), 0) - finite(data.get("battery_discharge_w"), 0), 0) if is_owned else 0
            electrical = (not data.get("grid_warning") and not data.get("inverter_warning") and phase in phase_budget
                          and phase_budget[phase] >= (0 if is_owned else watts)
                          and inverter_budget >= (0 if is_owned else watts) and (is_owned or not settling))
            if not is_owned and now.timestamp() - self._house_last_start < 120:
                electrical = False
            solar = available + covered >= watts * (.8 if is_owned else .95)
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
            decision = room_plan(house, room, now, current, solar, electrical)
            decision["subentry_id"] = subentry.subentry_id
            decision["commands_enabled"] = execute and room.get("reviewed", False)
            decision["confidence"] = learned.get("confidence", 0)
            decision["preheat_minutes"] = room.get("preheat_minutes", 90)
            decision["estimated_temperature"] = current
            decision["overshoot_c"] = max(0, min(finite(learned.get("overshoot_c"), 0), 1))
            if decision["heat_pump_mode"] == "heat" and current is not None and current >= decision["room_target"] - decision["overshoot_c"]:
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
            decisions.append(decision)
            if not execute or not room.get("reviewed"):
                continue
            if decision["heat_pump_mode"] != "off":
                mode = decision["heat_pump_mode"]
                if hp_state and mode in hp_state.attributes.get("hvac_modes", []):
                    target = self._house_calibrated_setpoint(hp, decision["heat_pump_target"], "cooling" if mode == "cool" else "heat_pump", learned, mode)
                    decision["device_setpoint"] = target
                    started = await self._house_command(hp, mode, target, now, compressor=True)
                    if started and "auto" in hp_state.attributes.get("fan_modes", []):
                        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": hp, "fan_mode": "auto"}, blocking=True)
                        self._house_owned[hp]["fan"] = "auto"
                        await self._house_save()
                    if started and not is_owned:
                        self._house_last_start = now.timestamp()
                        await self._house_save()
                    if not is_owned:
                        available = max(available - watts, 0)
                        phase_budget[phase] -= watts
                        inverter_budget -= watts
            elif is_owned and machine_mode == "auto":
                await self._house_command(hp, "off", None, now, compressor=True,
                                          safety_stop=emergency or not electrical or decision["source"] == "window_open_or_unknown" or
                                          (not solar and not (house.get("season") == "winter" and house.get("allow_economic_grid") and decision["economics_verified"])))
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

    def _house_calibrated_setpoint(self, entity, target, source, learned, mode):
        bias = learned.get("model", {}).get("sensor_bias", {}).get(entity + ":" + source, {})
        if bias.get("samples", 0) >= 12 and finite(bias.get("confidence"), 0) >= .3:
            target += max(-2, min(finite(bias.get("offset_c"), 0), 2))
        return min(target, 22) if mode == "heat" else max(20, min(target, 30))

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
        if mode not in ("normal", "home", "away", "weekend_away", "holiday"):
            raise ValueError("Invalid house situation")
        self._house_exception = {} if mode == "normal" else {"mode": mode, "expires_at": dt_util.utc_from_timestamp(dt_util.now().timestamp() + max(1, min(float(hours), 720)) * 3600).astimezone(dt_util.now().tzinfo).isoformat()}
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
        data = await super()._async_update_data()
        try:
            await self._async_house_plan(data, dt_util.now())
            self._house_error = None
        except Exception as err:
            self._house_error = type(err).__name__
            data["house_climate_error"] = self._house_error
            _LOGGER.warning("House climate planning failed: %s", type(err).__name__)
        return data

"""Optional combined gas/radiator and room heat-pump management."""
from datetime import timedelta
import logging

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .coordinator_v1520 import CasaESEnergyCoordinator as PreviousCoordinator
from .house_climate_plan import HOUSE_TYPE, ROOM_TYPE, finite, room_plan
from .engine_bridge import async_plan

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
        self._house_store = Store(hass, 1, f"casa_es_energy_manager.{entry.entry_id}.house_climate_control")

    async def async_initialize(self):
        saved = await self._house_store.async_load()
        if isinstance(saved, dict):
            self._house_owned = saved.get("owned", {})
            self._house_hold = saved.get("hold", {})
            self.house_gas_mode = "auto" if saved.get("gas_mode") == "auto" else "off"
            self._house_last_start = saved.get("last_start", 0)
            self.house_machine_modes.update(saved.get("machine_modes", {}))
            self._house_gas_stopped_at = saved.get("gas_stopped_at", 0)
        await super().async_initialize()

    def _house_config(self):
        houses = [s for s in self.entry.subentries.values() if s.subentry_type == HOUSE_TYPE]
        rooms = [s for s in self.entry.subentries.values() if s.subentry_type == ROOM_TYPE]
        return (dict(houses[0].data) if houses else None), rooms

    def _house_claimed_entities(self):
        house, rooms = self._house_config()
        if not house or not house.get("enabled") or not house.get("reviewed") or any(not r.data.get("reviewed") for r in rooms):
            return set()
        entities = {house.get("gas_entity", "")}
        for room in rooms:
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
            if not state or state.state in ("unknown", "unavailable"):
                return None
            value = finite(state.state)
            if state.attributes.get("unit_of_measurement") == "°F" and value is not None:
                value = (value - 32) * 5 / 9
            elif state.attributes.get("unit_of_measurement") not in ("°C", "C"):
                return None
            return value
        values = []
        for entity in room.get("radiator_entities") or [room.get("heat_pump_entity", "")]:
            state = self.hass.states.get(entity)
            if state and state.state not in ("unknown", "unavailable"):
                value = finite(state.attributes.get("current_temperature"))
                if value is not None:
                    if self.hass.config.units.temperature_unit == "°F":
                        value = (value - 32) * 5 / 9
                    values.append(value)
        return sum(values) / len(values) if values else None

    async def _house_save(self):
        await self._house_store.async_save({"owned": self._house_owned, "hold": self._house_hold,
                                            "gas_mode": self.house_gas_mode, "last_start": self._house_last_start,
                                            "machine_modes": self.house_machine_modes,
                                            "gas_stopped_at": self._house_gas_stopped_at})

    async def async_set_house_machine_mode(self, machine, mode):
        if machine not in self.house_machine_modes or mode not in ("auto", "manual", "off"):
            raise ValueError("Modalità climatizzatore non valida")
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
            (owned.get("target") is not None and actual is not None and abs(actual - owned["target"]) > .6)
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
            payload.update(hvac_mode=mode, temperature=target * 9 / 5 + 32 if self.hass.config.units.temperature_unit == "°F" else target)
            service = "set_temperature"
        await self.hass.services.async_call("climate", service, payload, blocking=True)
        self._house_owned[entity] = {"mode": mode, "target": target, "at": now.timestamp()}
        await self._house_save()
        return True

    async def _async_house_plan(self, data, now):
        house, rooms = self._house_config()
        if not house:
            data.update(house_climate_status="unconfigured", house_climate_rooms=[])
            return
        ready = bool(house.get("reviewed") and rooms and all(s.data.get("reviewed") for s in rooms))
        execute = bool(house.get("enabled") and ready and self.real_control_enabled)
        data["house_climate_status"] = "automatic" if execute else "observation"
        data["house_climate_profile_ready"] = ready
        data["house_climate_gas_mode"] = self.house_gas_mode
        data["house_climate_error"] = self._house_error
        zones = []
        for s in rooms:
            r = dict(s.data)
            hp = self.hass.states.get(r.get("heat_pump_entity", ""))
            valves = [self.hass.states.get(e) for e in r.get("radiator_entities", [])]
            hp_heat = bool(hp and hp.state == "heat")
            gas_state = self.hass.states.get(house.get("gas_entity", ""))
            gas_heat = bool(gas_state and gas_state.state == "heat" and any(v and v.attributes.get("hvac_action") == "heating" for v in valves))
            thermal_source = "combined" if hp_heat and gas_heat else "heat_pump" if hp_heat else "gas" if gas_heat else "off"
            contamination = []
            machine = r.get("machine", "none")
            if hp and hp.state not in ("off", "unknown", "unavailable") and self.house_machine_modes.get(machine) != "auto":
                contamination.append("manual_heat_pump")
            window = self.hass.states.get(r.get("window_entity", ""))
            if r.get("window_entity") and (not window or window.state != "off"):
                contamination.append("window_open_or_unknown")
            zones.append({"id": s.subentry_id, "config": r, "temperature": self._room_temperature(r),
                          "thermal_source": thermal_source, "contamination": contamination,
                          "solar_available": False, "electrical_ok": False})
        if now.timestamp() - getattr(self, "_house_engine_last_at", 0) >= 60:
            self._house_engine_result = await async_plan(self.hass, house, zones, {
                "pv_remaining_kwh": data.get("forecast_energy_to_target_kwh"),
                "battery_input_need_kwh": data.get("battery_input_energy_needed_kwh"),
                "house_remaining_kwh": data.get("base_load_energy_to_target_kwh"),
                "battery_capacity_kwh": self._config("battery_capacity_kwh"),
                "soc": data.get("battery_soc"), "target_soc": (data.get("v156_battery_allocation") or {}).get("target_soc")}, now)
            self._house_engine_last_at = now.timestamp()
        data["engine_state"] = self._house_engine_result.get("state", "DEGRADED")
        data["engine_reasons"] = self._house_engine_result.get("reasons", [])
        data["engine_recent_decisions"] = self._house_engine_result.get("recent_decisions", [])
        data["engine_energy_budget"] = self._house_engine_result.get("energy_budget", {})
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
            if learned.get("learning_status") == "thermal_rate_available":
                room["preheat_minutes"] = max(15, min(finite(learned.get("preheat_minutes"), 90), 240))
            current = self._room_temperature(room)
            hp = room.get("heat_pump_entity", "")
            hp_state = self.hass.states.get(hp)
            watts = finite(room.get("nominal_power_w"), 1200)
            phase = room.get("phase", "unknown")
            is_owned = hp in self._house_owned and hp_state and hp_state.state != "off"
            real_watts = finite(hp_state.attributes.get("realtime_power"), 0) if hp_state else 0
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
            decision["commands_enabled"] = execute
            window = self.hass.states.get(room.get("window_entity", ""))
            if room.get("window_entity") and (not window or window.state != "off"):
                decision.update(source="window_open_or_unknown", radiator_target=5, heat_pump_mode="off")
            if hp_state and hp_state.state not in ("off", "unknown", "unavailable") and not is_owned:
                decision.update(source="manual_heat_pump", radiator_target=None, heat_pump_mode="off")
            if current is not None and room.get("radiator_entities") and decision["radiator_target"] is not None and house.get("season") == "winter":
                if decision["source"] not in ("window_open_or_unknown", "profile_to_confirm"):
                    gas_sensors_valid = True
                    gas_demand |= current < decision["radiator_target"] - .3
            decisions.append(decision)
            if not execute:
                continue
            if decision["heat_pump_mode"] != "off":
                mode = decision["heat_pump_mode"]
                if hp_state and mode in hp_state.attributes.get("hvac_modes", []):
                    started = await self._house_command(hp, mode, decision["heat_pump_target"], now, compressor=True)
                    if started and not is_owned:
                        self._house_last_start = now.timestamp()
                        await self._house_save()
                    if not is_owned:
                        available = max(available - watts, 0)
                        phase_budget[phase] -= watts
                        inverter_budget -= watts
            elif is_owned and machine_mode == "auto":
                await self._house_command(hp, "off", None, now, compressor=True,
                                          safety_stop=not electrical or decision["source"] == "window_open_or_unknown" or
                                          (not solar and not (house.get("season") == "winter" and house.get("allow_economic_grid") and decision["economics_verified"])))
            if (self.house_gas_mode == "auto" and decision["radiator_target"] is not None and house.get("season") == "winter"
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
                    await self._house_command(valve, "heat", decision["radiator_target"], now)
        data["house_climate_rooms"] = decisions
        demanding_rooms = set()
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
            strong_demand = any(d["subentry_id"] in demanding_rooms and d["current_temperature"] is not None
                                and d["radiator_target"] is not None and d["radiator_target"] - d["current_temperature"] >= 1
                                for d in decisions)
            gas_demand = bool(house.get("hydraulics_confirmed") and (len(demanding_rooms) >= 2 or strong_demand)
                              and now.timestamp() - self._house_gas_stopped_at >= finite(house.get("dissipation_seconds"), 180))
        data["house_climate_gas_demand_room_count"] = len(demanding_rooms)
        data["house_climate_gas_demand"] = gas_demand
        if execute and self.house_gas_mode == "auto" and house.get("gas_entity"):
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


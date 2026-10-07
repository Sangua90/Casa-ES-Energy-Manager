"""Schedules, heat costs, inactive rollout and control ownership."""
import ast
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("house_plan", ROOT / "custom_components/casa_es_energy_manager/house_climate_plan.py")
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)
NOW = datetime(2026, 10, 7, 21, tzinfo=timezone(timedelta(hours=2)))


class PlanTests(unittest.TestCase):
    def room(self):
        return {"name": "Bedroom", "reviewed": True, "radiator_entities": ["climate.valve"],
                "heat_pump_entity": "climate.hp", "base_temperature": 17, "comfort_temperature": 21,
                "weekday_enabled": True, "weekday_start": "21:00:00", "weekday_end": "07:00:00",
                "weekend_enabled": True, "weekend_start": "22:30:00", "weekend_end": "10:00:00",
                "preheat_minutes": 90, "heat_pump_cop": 4, "cop_confirmed": True}

    def test_overnight_uses_start_day_even_at_weekend_boundary(self):
        room = self.room()
        saturday = NOW.replace(day=10, hour=8)
        self.assertEqual(plan.occupancy(room, saturday), (False, False))
        sunday = NOW.replace(day=11, hour=8)
        self.assertEqual(plan.occupancy(room, sunday), (True, False))
        monday = NOW.replace(day=12, hour=8)
        self.assertEqual(plan.occupancy(room, monday), (True, False))

    def test_preheat_and_two_daily_periods(self):
        room = self.room()
        self.assertEqual(plan.occupancy(room, NOW.replace(hour=20)), (True, True))
        room.update(weekday_second_enabled=True, weekday_second_start="12:00:00", weekday_second_end="13:30:00")
        self.assertTrue(plan.occupancy(room, NOW.replace(hour=12))[0])
        self.assertFalse(plan.occupancy(room, NOW.replace(hour=16))[0])

    def test_manual_room_has_no_invented_schedule(self):
        room = self.room()
        room["manual_only"] = True
        self.assertEqual(plan.occupancy(room, NOW), (False, False))

    def test_verified_cost_comparison_is_per_unit_of_heat(self):
        house = {"season": "winter", "electricity_price": .3, "gas_price": 1,
                 "gas_energy_kwh_sm3": 10, "gas_efficiency": .9, "economics_confirmed": True,
                 "allow_economic_grid": True}
        result = plan.room_plan(house, self.room(), NOW, 18, False, True)
        self.assertAlmostEqual(result["heat_pump_eur_kwh_heat"], .075)
        self.assertAlmostEqual(result["gas_eur_kwh_heat"], 1 / 9)
        self.assertEqual(result["source"], "economic_heat_pump")
        self.assertEqual(result["radiator_target"], 17)

    def test_unverified_cop_or_cost_never_select_grid_heat_pump(self):
        for house, room in (({"season": "winter", "allow_economic_grid": True}, self.room()),
                            ({"season": "winter", "allow_economic_grid": True, "economics_confirmed": True},
                             {**self.room(), "cop_confirmed": False})):
            self.assertEqual(plan.room_plan(house, room, NOW, 18, False, True)["source"], "gas_comfort")

    def test_summer_and_shoulder_require_solar_and_no_gas(self):
        for season, temp, mode in (("summer", 29, "cool"), ("shoulder", 18, "heat")):
            result = plan.room_plan({"season": season}, self.room(), NOW, temp, True, True)
            self.assertEqual(result["heat_pump_mode"], mode)
            self.assertIsNone(result["radiator_target"])
            result = plan.room_plan({"season": season}, self.room(), NOW, temp, False, True)
            self.assertEqual(result["heat_pump_mode"], "off")

    def test_missing_temperature_and_unreviewed_profile_block(self):
        self.assertIsNone(plan.room_plan({"season": "winter"}, self.room(), NOW, None, True, True)["radiator_target"])
        result = plan.room_plan({"season": "winter"}, {**self.room(), "reviewed": False}, NOW, 12, True, True)
        self.assertEqual(result["source"], "profile_to_confirm")
        self.assertEqual(result["heat_pump_mode"], "off")


source = (ROOT / "custom_components/casa_es_energy_manager/coordinator_v1522.py").read_text()
classes = ast.Module(body=[n for n in ast.parse(source).body if isinstance(n, ast.ClassDef)], type_ignores=[])
namespace = {"PreviousCoordinator": object, "HOUSE_TYPE": plan.HOUSE_TYPE, "ROOM_TYPE": plan.ROOM_TYPE,
             "finite": plan.finite, "room_plan": plan.room_plan,
             "dt_util": SimpleNamespace(parse_datetime=datetime.fromisoformat), "timedelta": timedelta, "async_plan": AsyncMock(return_value={"state": "DEGRADED", "zones": {}})}
exec(compile(classes, "coordinator_v1522.py", "exec"), namespace)
Coordinator = namespace["CasaESEnergyCoordinator"]


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def setup_control(self):
        c = object.__new__(Coordinator)
        c.real_control_enabled = True
        c._house_owned, c._house_hold = {}, {}
        c._house_error, c._last_thermal_at = None, None
        c.house_gas_mode, c._house_last_start = "auto", 0
        c.house_machine_modes = {"salotto": "auto", "ester": "manual", "p1": "auto"}
        c._house_open_since, c._house_gas_stopped_at, c._house_engine_result = {}, 0, {}
        c._config = lambda key: None
        c._virtual_surplus_opportunity = lambda data: False
        c._house_store = SimpleNamespace(async_save=AsyncMock())
        states = {"climate.hp": SimpleNamespace(state="off", last_changed=NOW - timedelta(hours=2),
                                                attributes={"hvac_modes": ["heat", "cool", "off"], "temperature": 21,
                                                            "current_temperature": 18, "min_temp": 16, "max_temp": 30}),
                  "climate.valve": SimpleNamespace(state="heat", last_changed=NOW - timedelta(hours=2),
                                                   attributes={"hvac_modes": ["heat", "off"], "temperature": 17, "current_temperature": 18}),
                  "climate.gas": SimpleNamespace(state="off", last_changed=NOW - timedelta(hours=2),
                                                 attributes={"hvac_modes": ["heat", "off"], "temperature": 26, "current_temperature": 24})}
        c.hass = SimpleNamespace(states=SimpleNamespace(get=states.get),
                                 config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°C")),
                                 services=SimpleNamespace(async_call=AsyncMock()))
        room = {**PlanTests().room(), "phase": "l1", "nominal_power_w": 1200, "machine": "salotto"}
        house = {"season": "winter", "enabled": False, "reviewed": True, "gas_entity": "climate.gas", "hydraulics_confirmed": True, "valve_open_seconds": 30}
        c._house_config = lambda: (house, [SimpleNamespace(data=room, subentry_id="room")])
        data = {"grid_export_w": 1500, "phase_l1_headroom_w": 3000, "inverter_headroom_w": 4000}
        return c, house, room, data, states

    async def test_observation_makes_no_commands(self):
        c, _, _, data, _ = self.setup_control()
        await c._async_house_plan(data, NOW)
        c.hass.services.async_call.assert_not_awaited()
        self.assertEqual(data["house_climate_status"], "observation")

    async def test_unreviewed_room_prevents_all_automatic_commands(self):
        c, house, room, data, _ = self.setup_control()
        house["enabled"], room["reviewed"] = True, False
        await c._async_house_plan(data, NOW)
        c.hass.services.async_call.assert_not_awaited()

    async def test_phase_unknown_never_starts_heat_pump(self):
        c, house, room, data, _ = self.setup_control()
        house["enabled"], room["phase"] = True, "unknown"
        await c._async_house_plan(data, NOW)
        self.assertFalse(any(call.args[2]["entity_id"] == "climate.hp" for call in c.hass.services.async_call.call_args_list))

    async def test_manual_heat_pump_not_adopted_or_stopped(self):
        c, house, _, data, states = self.setup_control()
        house["enabled"] = True
        states["climate.hp"].state = "cool"
        await c._async_house_plan(data, NOW)
        c.hass.services.async_call.assert_not_awaited()
        self.assertEqual(data["house_climate_rooms"][0]["source"], "manual_heat_pump")

    async def test_compressor_minimum_off_and_manual_setpoint_hold(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].last_changed = NOW - timedelta(minutes=2)
        self.assertFalse(await c._house_command("climate.hp", "heat", 21, NOW, compressor=True))
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21, "at": (NOW - timedelta(minutes=30)).timestamp()}
        states["climate.hp"].state = "heat"
        states["climate.hp"].attributes["temperature"] = 23
        self.assertFalse(await c._house_command("climate.hp", "heat", 21, NOW, compressor=True))
        self.assertNotIn("climate.hp", c._house_owned)
        self.assertGreater(c._house_hold["climate.hp"], NOW.timestamp())

    async def test_no_boiler_fire_against_closed_valves(self):
        c, house, _, data, _ = self.setup_control()
        house["enabled"] = True
        data["grid_export_w"] = 0
        await c._async_house_plan(data, NOW)
        self.assertFalse(data["house_climate_gas_demand"])
        self.assertFalse(any(call.args[2]["entity_id"] == "climate.gas" for call in c.hass.services.async_call.call_args_list))

    async def test_dhw_and_non_climate_entities_rejected(self):
        c, _, _, _, _ = self.setup_control()
        for entity in ("water_heater.ariston_boiler", "switch.ariston_power"):
            self.assertFalse(await c._house_command(entity, "off", None, NOW))
        c.hass.services.async_call.assert_not_awaited()

    async def test_two_valves_in_one_room_small_demand_does_not_fire_boiler(self):
        c, house, room, data, states = self.setup_control()
        house["enabled"] = True
        data["grid_export_w"] = 0
        states["climate.valve"].attributes["temperature"] = 21
        states["climate.valve2"] = SimpleNamespace(state="heat", last_changed=NOW - timedelta(hours=2),
            attributes={"hvac_modes": ["heat", "off"], "temperature": 21, "current_temperature": 18})
        room["radiator_entities"].append("climate.valve2")
        for entity in ("climate.valve", "climate.valve2"):
            states[entity].attributes.update(current_temperature=20.5, hvac_action="heating")
            c._house_open_since[entity] = (NOW - timedelta(minutes=10)).timestamp()
        await c._async_house_plan(data, NOW)
        self.assertEqual(data["house_climate_gas_demand_room_count"], 1)
        self.assertFalse(data["house_climate_gas_demand"])

    async def test_two_rooms_fire_and_single_remaining_room_stops_boiler(self):
        c, house, room, data, states = self.setup_control()
        house["enabled"] = True
        data["grid_export_w"] = 0
        states["climate.valve"].attributes["temperature"] = 21
        states["climate.valve2"] = SimpleNamespace(state="heat", last_changed=NOW - timedelta(hours=2),
            attributes={"hvac_modes": ["heat", "off"], "temperature": 21, "current_temperature": 18})
        room2 = {**room, "radiator_entities": ["climate.valve2"], "heat_pump_entity": ""}
        for entity in ("climate.valve", "climate.valve2"):
            states[entity].attributes.update(current_temperature=20.5, hvac_action="heating")
            c._house_open_since[entity] = (NOW - timedelta(minutes=10)).timestamp()
        c._house_config = lambda: (house, [SimpleNamespace(data=room, subentry_id="one"),
                                         SimpleNamespace(data=room2, subentry_id="two")])
        await c._async_house_plan(data, NOW)
        self.assertTrue(data["house_climate_gas_demand"])
        self.assertEqual(data["house_climate_gas_demand_room_count"], 2)
        self.assertTrue(any(call.args[2].get("entity_id") == "climate.gas" and
                            call.args[2].get("hvac_mode") == "heat"
                            for call in c.hass.services.async_call.call_args_list))
        states["climate.gas"].state = "heat"
        states["climate.valve2"].attributes["hvac_action"] = "idle"
        c.hass.services.async_call.reset_mock()
        await c._async_house_plan(data, NOW + timedelta(seconds=30))
        self.assertFalse(data["house_climate_gas_demand"])
        self.assertTrue(any(call.args[2] == {"entity_id": "climate.gas", "hvac_mode": "off"}
                            for call in c.hass.services.async_call.call_args_list))

    async def test_gas_off_button_only_commands_heating_thermostat(self):
        c, _, _, _, _ = self.setup_control()
        c.async_request_refresh = AsyncMock()
        await c.async_set_house_gas_mode("off")
        c.hass.services.async_call.assert_awaited_once_with("climate", "set_hvac_mode", {"entity_id": "climate.gas", "hvac_mode": "off"}, blocking=True)
        self.assertEqual(c.house_gas_mode, "off")

    async def test_manual_gas_preserves_thermostat_and_valves(self):
        c, house, _, data, _ = self.setup_control()
        house["enabled"] = True
        c.house_gas_mode = "manual"
        data["grid_export_w"] = 0
        await c._async_house_plan(data, NOW)
        c.hass.services.async_call.assert_not_awaited()

    async def test_recent_compressor_start_blocks_another_start(self):
        c, house, _, data, _ = self.setup_control()
        house["enabled"] = True
        c._house_last_start = (NOW - timedelta(seconds=30)).timestamp()
        await c._async_house_plan(data, NOW)
        self.assertFalse(any(call.args[2]["entity_id"] == "climate.hp" for call in c.hass.services.async_call.call_args_list))

    async def test_unknown_valve_action_cannot_prove_open_hydraulic_path(self):
        c, house, _, data, states = self.setup_control()
        house["enabled"] = True
        states["climate.valve"].attributes["temperature"] = 21
        data["grid_export_w"] = 0
        await c._async_house_plan(data, NOW)
        self.assertFalse(data["house_climate_gas_demand"])

    async def test_strong_single_zone_requires_hydraulic_confirmation_and_open_delay(self):
        c, house, _, data, states = self.setup_control()
        house["enabled"] = True
        data["grid_export_w"] = 0
        states["climate.valve"].attributes.update(temperature=21, hvac_action="heating")
        await c._async_house_plan(data, NOW)
        self.assertFalse(data["house_climate_gas_demand"])
        house["hydraulics_confirmed"] = False
        await c._async_house_plan(data, NOW + timedelta(minutes=5))
        self.assertFalse(data["house_climate_gas_demand"])
        house["hydraulics_confirmed"] = True
        await c._async_house_plan(data, NOW + timedelta(minutes=6))
        self.assertTrue(data["house_climate_gas_demand"])

    async def test_multisplit_incompatible_manual_head_blocks_start(self):
        c, house, room, data, states = self.setup_control()
        house["enabled"] = True
        room["machine"] = "p1"
        states["climate.other"] = SimpleNamespace(state="cool", attributes={"current_temperature": 23})
        other = {"name": "Other", "reviewed": True, "manual_only": True,
                 "machine": "p1", "heat_pump_entity": "climate.other"}
        c._house_config = lambda: (house, [SimpleNamespace(data=room, subentry_id="one"),
                                         SimpleNamespace(data=other, subentry_id="two")])
        await c._async_house_plan(data, NOW)
        self.assertFalse(any(call.args[2]["entity_id"] == "climate.hp" for call in c.hass.services.async_call.call_args_list))

    async def test_machine_manual_does_not_stop_previously_owned_heat_pump(self):
        c, house, _, data, states = self.setup_control()
        house["enabled"] = True
        c.house_machine_modes["salotto"] = "manual"
        states["climate.hp"].state = "heat"
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21, "at": (NOW - timedelta(minutes=30)).timestamp()}
        await c._async_house_plan(data, NOW)
        self.assertFalse(any(call.args[2]["entity_id"] == "climate.hp" for call in c.hass.services.async_call.call_args_list))


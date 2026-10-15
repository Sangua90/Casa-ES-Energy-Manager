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

    def test_nominal_cop_does_not_authorize_cold_or_missing_weather(self):
        room = {**self.room(), "heat_pump_cop": 3.83, "cop_policy": "nominal_guarded"}
        for temperature in (None, -5, 6.9, 25):
            cost = plan.heat_costs({"economics_confirmed": True, "outdoor_temperature": temperature}, room)
            self.assertFalse(cost["economics_verified"])
        cost = plan.heat_costs({"economics_confirmed": True, "outdoor_temperature": 7}, room)
        self.assertTrue(cost["economics_verified"])
        self.assertAlmostEqual(cost["cop_used"], 3.83 * .85)

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


source = (ROOT / "custom_components/casa_es_energy_manager/coordinator_v1522.py").read_text(encoding="utf-8")
classes = ast.Module(body=[n for n in ast.parse(source).body if isinstance(n, ast.ClassDef)], type_ignores=[])
from test_solar_trajectory_release import ns as solar_ns
namespace = {"PreviousCoordinator": solar_ns["Controller"], "HOUSE_TYPE": plan.HOUSE_TYPE, "ROOM_TYPE": plan.ROOM_TYPE,
             "auxiliary_active": plan.auxiliary_active, "situation": plan.situation, "celsius": plan.celsius, "finite": plan.finite, "room_plan": plan.room_plan,
             "dt_util": SimpleNamespace(parse_datetime=datetime.fromisoformat), "timedelta": timedelta, "async_plan": AsyncMock(return_value={"state": "DEGRADED", "zones": {}})}
exec(compile(classes, "coordinator_v1522.py", "exec"), namespace)
Coordinator = namespace["CasaESEnergyCoordinator"]


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_room_storage_margins_persist_without_changing_other_rooms(self):
        c,_,_,_,_=self.setup_control()
        c.async_request_refresh=AsyncMock()
        await c.async_set_house_room_storage_margin('room','heat',1.5)
        await c.async_set_house_room_storage_margin('room','cool',.5)
        self.assertEqual(c.house_room_storage_margins('room',{}),{'solar_storage_margin_c':1.5,'solar_storage_cooling_margin_c':.5})
        self.assertEqual(c.house_room_storage_margins('other',{})['solar_storage_margin_c'],5)
        self.assertEqual(c._house_store.async_save.call_args.args[0]['room_storage']['room'],{'heat':1.5,'cool':.5})
        with self.assertRaises(ValueError):
            await c.async_set_house_room_storage_margin('room','cool',4)
    async def test_independent_thermometer_replaces_trv_reference(self):
        c,house,room,data,states=self.setup_control()
        room['temperature_entity']='sensor.room_temperature'
        states['sensor.room_temperature']=SimpleNamespace(state='24.3',last_reported=NOW,attributes={'unit_of_measurement':'°C'})
        states['climate.hp'].attributes['current_temperature']=28
        c._house_reference_offsets={'climate.hp':{'offset':9,'at':NOW.timestamp()}}
        offset,quality=await c._house_trv_reference(house,room,NOW)
        self.assertAlmostEqual(offset,3.7)
        self.assertEqual(quality,'independent_room_sensor')
        self.assertEqual(c._house_reference_offsets['climate.hp']['reference'],'sensor.room_temperature')
        self.assertEqual(c._room_temperature(room),24.3)

    async def test_independent_temperature_is_not_replaced_by_old_fusion(self):
        c,house,room,data,states=self.setup_control()
        house.update(enabled=True,hydraulics_confirmed=False)
        room['temperature_entity']='sensor.room_temperature'
        states['sensor.room_temperature']=SimpleNamespace(state='24.3',last_reported=NOW,attributes={'unit_of_measurement':'°C'})
        c._house_engine_result={'zones':{'room':{'estimated_temperature':22,'estimate_quality':'calibrated_fusion'}}}
        c._house_engine_last_at=NOW.timestamp()
        await c._async_house_plan(data,NOW)
        self.assertEqual(data['house_climate_rooms'][0]['current_temperature'],24.3)

    async def test_unacknowledged_target_is_not_a_manual_edit_and_can_stop(self):
        c,_,_,_,states=self.setup_control()
        states['climate.hp'].state='heat'
        states['climate.hp'].attributes['temperature']=26.5
        c._house_owned['climate.hp']={'mode':'heat','target':28,'at':NOW.timestamp()-1500,
            'mode_at':NOW.timestamp()-1500,'acknowledged':False,'command_attempts':3}
        self.assertTrue(await c._house_command('climate.hp','off',None,NOW,compressor=True))
        self.assertNotIn('climate.hp',c._house_hold)
        self.assertEqual(c._house_owned['climate.hp']['mode'],'off')

    async def test_failed_command_retries_then_stops_with_cooldown_after_minimum(self):
        c,_,_,_,states=self.setup_control()
        states['climate.hp'].state='heat'
        states['climate.hp'].attributes['temperature']=26.5
        c._house_owned['climate.hp']={'mode':'heat','target':28,'at':NOW.timestamp()-900,
            'mode_at':NOW.timestamp()-900,'acknowledged':False,'command_attempts':3,
            'unconfirmed_since':NOW.timestamp()-900}
        self.assertFalse(await c._house_command('climate.hp','heat',28,NOW,compressor=True))
        c.hass.services.async_call.assert_not_awaited()
        later=NOW+timedelta(minutes=5)
        self.assertFalse(await c._house_command('climate.hp','heat',28,later,compressor=True))
        self.assertEqual(c._house_owned['climate.hp']['mode'],'off')
        self.assertEqual(c._house_owned['climate.hp']['fault_until'],later.timestamp()+600)
        # If the unit did not obey OFF, do not wait another twenty minutes.
        self.assertFalse(await c._house_command('climate.hp','off',None,later+timedelta(minutes=2),compressor=True))
        self.assertTrue(await c._house_command('climate.hp','off',None,later+timedelta(minutes=3),compressor=True))

    async def test_priority_defaults_overrides_and_persistence(self):
        c, _, room, _, _ = self.setup_control()
        names=['Salotto','Camera ES','Palestra','Sottoscala','Camera Ester']
        self.assertEqual([c.house_room_priority(str(i),{**room,'name':n})
                          for i,n in enumerate(names)],[1,2,3,4,5])
        c.async_request_refresh=AsyncMock()
        await c.async_set_house_room_priority('palestra',1)
        saved=c._house_store.async_save.call_args.args[0]
        self.assertEqual(saved['priorities'],{'palestra':1})
        self.assertEqual(c.house_room_priority('palestra',{'name':'Palestra'}),1)
        with self.assertRaises(ValueError):
            await c.async_set_house_room_priority('palestra',6)

    async def test_emergency_sheds_lowest_priority_first(self):
        c, house, room, data, states = self.setup_control()
        members=[]
        template=states['climate.hp']
        for i,name in enumerate(['Salotto','Camera ES','Palestra','Sottoscala','Camera Ester']):
            entity='climate.head'+str(i)
            states[entity]=SimpleNamespace(state='heat',attributes=dict(template.attributes))
            members.append(SimpleNamespace(data={**room,'name':name,'heat_pump_entity':entity},subentry_id=str(i)))
        data.update(load_power_w=10000,inverter_power_limit_w=10000)
        await c._house_power_guard(house,members,data,NOW)
        self.assertEqual(c.hass.services.async_call.call_args.args[2]['entity_id'],'climate.head4')

    def setup_control(self):
        c = object.__new__(Coordinator)
        c.real_control_enabled = True
        c._house_owned, c._house_hold = {}, {}
        # Other control tests isolate their admission/dwell behavior; dedicated
        # stability tests below exercise the production ten-minute defaults.
        c._house_surplus_delays = {"start": 0, "stop": 10}
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

    async def test_engine_outage_keeps_conservative_local_preparation(self):
        c, house, _, data, _ = self.setup_control()
        house.update(enabled=True, hydraulics_confirmed=False)
        await c._async_house_plan(data, NOW)
        self.assertEqual(data["house_climate_rooms"][0]["preheat_minutes"], 480)
        self.assertEqual(data["engine_state"], "DEGRADED")

    async def test_unverified_hydraulics_never_changes_valves_even_enabled(self):
        c, house, _, data, _ = self.setup_control()
        house.update(enabled=True, hydraulics_confirmed=False)
        data["grid_export_w"] = 0
        await c._async_house_plan(data, NOW)
        self.assertFalse(any(call.args[2].get("entity_id") in ("climate.valve", "climate.gas") for call in c.hass.services.async_call.call_args_list))

    async def test_grid_emergency_waits_15_seconds_then_sheds_manual_split(self):
        c, house, _, data, states = self.setup_control()
        house["enabled"] = True
        c.house_machine_modes["salotto"] = "manual"
        states["climate.hp"].state = "heat"
        data.update(grid_import_w=6200, grid_power_limit_w=6000)
        await c._async_house_plan(data, NOW)
        c.hass.services.async_call.assert_not_awaited()
        await c._async_house_plan(data, NOW + timedelta(seconds=16))
        self.assertEqual(data["engine_state"], "SAFE")
        self.assertTrue(any(call.args[2] == {"entity_id": "climate.hp", "hvac_mode": "off"} for call in c.hass.services.async_call.call_args_list))

    async def test_one_pending_room_does_not_block_verified_room(self):
        c, house, room, data, _ = self.setup_control()
        house.update(enabled=True, hydraulics_confirmed=False)
        pending = {**room, "reviewed": False, "heat_pump_entity": "", "radiator_entities": []}
        c._house_config = lambda: (house, [SimpleNamespace(data=room, subentry_id="ready"), SimpleNamespace(data=pending, subentry_id="pending")])
        await c._async_house_plan(data, NOW)
        self.assertEqual(data["house_climate_status"], "automatic")
        self.assertFalse(data["house_climate_rooms"][1]["commands_enabled"])

    async def test_window_contact_blocks_solar_start(self):
        c, house, room, data, states = self.setup_control()
        house.update(enabled=True, hydraulics_confirmed=False, season="shoulder")
        room["window_entities"] = ["binary_sensor.window"]
        states["binary_sensor.window"] = SimpleNamespace(state="on", attributes={})
        await c._async_house_plan(data, NOW)
        self.assertEqual(data["house_climate_rooms"][0]["source"], "window_open_or_unknown")
        c.hass.services.async_call.assert_not_awaited()

    async def test_native_setpoint_step_is_respected(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].attributes["target_temp_step"] = .5
        await c._house_command("climate.hp", "heat", 20.7, NOW, compressor=True)
        self.assertEqual(c.hass.services.async_call.call_args.args[2]["temperature"], 20.5)

    async def test_manual_half_degree_change_releases_ownership(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].state = "heat"
        states["climate.hp"].attributes.update(temperature=21.5, target_temp_step=.5)
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21, "at": (NOW - timedelta(minutes=30)).timestamp()}
        await c._house_command("climate.hp", "heat", 21, NOW, compressor=True)
        c.hass.services.async_call.assert_not_awaited()
        self.assertNotIn("climate.hp", c._house_owned)

    async def test_recent_setpoint_change_does_not_reset_compressor_dwell(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].state = "heat"
        states["climate.hp"].attributes["temperature"] = 21
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21, "at": (NOW - timedelta(minutes=2)).timestamp(), "mode_at": (NOW - timedelta(minutes=30)).timestamp()}
        await c._house_command("climate.hp", "off", None, NOW, compressor=True)
        self.assertEqual(c.hass.services.async_call.call_args.args[2]["hvac_mode"], "off")

    async def test_restart_downtime_counts_toward_minimum_on(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].state = "heat"
        # HA restarts after ten minutes and returns five minutes later.
        states["climate.hp"].last_changed = NOW
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21,
            "at": (NOW - timedelta(minutes=15)).timestamp(),
            "mode_at": (NOW - timedelta(minutes=15)).timestamp()}
        self.assertFalse(await c._house_command("climate.hp", "off", None, NOW, compressor=True))
        self.assertTrue(await c._house_command("climate.hp", "off", None, NOW + timedelta(minutes=5), compressor=True))

    async def test_restart_downtime_counts_toward_five_minute_off(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].last_changed = NOW
        # Restored ownership survives a five-minute HA outage.
        c._house_owned["climate.hp"] = {"mode": "off", "target": None,
            "at": (NOW - timedelta(minutes=5)).timestamp(),
            "mode_at": (NOW - timedelta(minutes=5)).timestamp()}
        self.assertTrue(await c._house_command("climate.hp", "heat", 21, NOW, compressor=True))

    async def test_four_minutes_off_cannot_restart(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].last_changed = NOW - timedelta(minutes=4)
        self.assertFalse(await c._house_command("climate.hp", "heat", 21, NOW, compressor=True))
        self.assertTrue(await c._house_command("climate.hp", "heat", 21, NOW + timedelta(minutes=1), compressor=True))

    async def test_electrical_safety_can_stop_before_twenty_minutes(self):
        c, _, _, _, states = self.setup_control()
        states["climate.hp"].state = "heat"
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21, "at": NOW.timestamp(), "mode_at": NOW.timestamp()}
        self.assertTrue(await c._house_command("climate.hp", "off", None, NOW, compressor=True, safety_stop=True))

    async def test_surplus_loss_respects_twenty_minutes_on(self):
        c, house, room, data, states = self.setup_control()
        house.update(enabled=True, hydraulics_confirmed=False, season="shoulder")
        states["climate.hp"].state = "heat"
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21,
            "at": NOW.timestamp(), "mode_at": NOW.timestamp()}
        data["grid_export_w"] = 0
        await c._async_house_plan(data, NOW + timedelta(minutes=2))
        self.assertFalse(any(call.args[1] == "set_hvac_mode" and
            call.args[2].get("hvac_mode") == "off"
            for call in c.hass.services.async_call.call_args_list))
        await c._async_house_plan(data, NOW + timedelta(minutes=20))
        self.assertTrue(any(call.args[1] == "set_hvac_mode" and
            call.args[2].get("hvac_mode") == "off"
            for call in c.hass.services.async_call.call_args_list))

    async def test_room_ceiling_does_not_bypass_twenty_minutes_on(self):
        c, house, room, data, states = self.setup_control()
        house.update(enabled=True, hydraulics_confirmed=False, season="shoulder")
        states["climate.hp"].state = "heat"
        states["climate.valve"].attributes["current_temperature"] = 27
        c._house_owned["climate.hp"] = {"mode": "heat", "target": 21,
            "at": NOW.timestamp(), "mode_at": NOW.timestamp()}
        await c._async_house_plan(data, NOW + timedelta(minutes=2))
        self.assertFalse(any(call.args[1] == "set_hvac_mode" and
            call.args[2].get("hvac_mode") == "off"
            for call in c.hass.services.async_call.call_args_list))

    async def test_bias_uses_only_qualified_context_and_heat_ceiling(self):
        c, _, _, _, _ = self.setup_control()
        learned = {"model": {"sensor_bias": {"climate.hp:heat_pump": {"samples": 20, "confidence": .5, "offset_c": 3}}}}
        self.assertEqual(c._house_calibrated_setpoint("climate.hp", 21, "heat_pump", learned, "heat"), 22)
        self.assertEqual(c._house_calibrated_setpoint("climate.hp", 21, "cooling", learned, "cool"), 21)

    async def test_explicit_auto_clears_hold_and_adopts_manual_running_split(self):
        c, _, _, _, states = self.setup_control()
        c.house_machine_modes["salotto"] = "manual"
        states["climate.hp"].state = "heat"
        c._house_hold["climate.hp"] = (NOW + timedelta(hours=2)).timestamp()
        c.async_request_refresh = AsyncMock()
        old = namespace["dt_util"]
        namespace["dt_util"] = SimpleNamespace(parse_datetime=datetime.fromisoformat, now=lambda: NOW)
        try:
            await c.async_set_house_machine_mode("salotto", "auto")
        finally:
            namespace["dt_util"] = old
        self.assertNotIn("climate.hp", c._house_hold)
        self.assertEqual(c._house_owned["climate.hp"]["mode"], "heat")
        c.hass.services.async_call.assert_not_awaited()

    async def test_unavailable_independent_sensor_falls_back_to_valve_then_split(self):
        c, _, room, _, states = self.setup_control()
        room["temperature_entity"] = "sensor.room"
        states["sensor.room"] = SimpleNamespace(state="unavailable", attributes={})
        self.assertEqual(c._room_temperature(room), 18)
        states["climate.valve"].state = "unavailable"
        states["climate.hp"].attributes["current_temperature"] = 19
        self.assertEqual(c._room_temperature(room), 19)
        states["climate.hp"].state = "unavailable"
        self.assertIsNone(c._room_temperature(room))

    async def test_independent_sensor_unit_and_glitch_checked(self):
        c, _, room, _, states = self.setup_control()
        room["temperature_entity"] = "sensor.room"
        states["sensor.room"] = SimpleNamespace(state="68", attributes={"unit_of_measurement": "°F"})
        self.assertEqual(c._room_temperature(room), 20)
        states["sensor.room"].attributes["unit_of_measurement"] = "°C"
        self.assertEqual(c._room_temperature(room), 18)

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

    async def test_two_valves_in_one_room_small_demand_fires_boiler(self):
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
        self.assertEqual(data["house_climate_gas_demand_valve_count"], 2)
        self.assertTrue(data["house_climate_gas_demand"])

    async def test_two_valves_in_one_room_large_demand_fires_boiler(self):
        c, house, room, data, states = self.setup_control()
        house["enabled"] = True
        data["grid_export_w"] = 0
        states["climate.valve"].attributes["temperature"] = 21
        states["climate.valve2"] = SimpleNamespace(state="heat", last_changed=NOW - timedelta(hours=2),
            attributes={"hvac_modes": ["heat", "off"], "temperature": 21, "current_temperature": 18})
        room["radiator_entities"].append("climate.valve2")
        for entity in ("climate.valve", "climate.valve2"):
            states[entity].attributes.update(current_temperature=18, hvac_action="heating")
            c._house_open_since[entity] = (NOW - timedelta(minutes=10)).timestamp()
        await c._async_house_plan(data, NOW)
        self.assertEqual(data["house_climate_gas_demand_room_count"], 1)
        self.assertEqual(data["house_climate_gas_demand_valve_count"], 2)
        self.assertTrue(data["house_climate_gas_demand"])

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

    async def test_strong_single_zone_stays_off_even_after_hydraulic_confirmation_and_open_delay(self):
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
        self.assertFalse(data["house_climate_gas_demand"])

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


class SpecialModesTests(unittest.TestCase):
    room=PlanTests.room
    def test_guest_uses_weekend_on_workday(self):
        room=self.room();now=NOW.replace(hour=9)
        normal=plan.target_level({},room,now)
        guests=plan.target_level({"exception":{"mode":"guest","persistent":True}},room,now)
        self.assertNotEqual(normal[1],"comfort")
        self.assertEqual(guests[1],"comfort")
        self.assertEqual(plan.situation({"exception":{"mode":"guest","persistent":True}},now+timedelta(days=30)),"guest")

    def test_guest_manual_room_and_electrical_guards_remain(self):
        house={"season":"summer","exception":{"mode":"guest","persistent":True}}
        room=self.room();room["manual_only"]=True
        self.assertFalse(plan.target_level(house,room,NOW)[2])
        room["manual_only"]=False
        self.assertEqual(plan.room_plan(house,room,NOW.replace(hour=9),30,True,False)["heat_pump_mode"],"off")

    def test_vacation_persists_and_keeps_radiator_at_maintenance(self):
        house={"season":"winter","exception":{"mode":"holiday","persistent":True}}
        room=self.room();room['maintenance_temperature']=16
        for offset in (0,8,30):
            now=NOW+timedelta(days=offset)
            self.assertEqual(plan.situation(house,now),"holiday")
            result=plan.room_plan(house,room,now,20,True,True)
            self.assertEqual(result["room_target"],16)
            self.assertEqual(result["radiator_target"],16)
            self.assertEqual(result["heat_pump_mode"],"off")

    def test_normal_restores_weekday_schedule_and_deadlines(self):
        room=self.room();now=NOW.replace(hour=9)
        self.assertEqual(plan.next_deadline(room,now).hour,21)
        self.assertEqual(plan.next_deadline(room,now,non_working_day=True).hour,22)
        self.assertEqual(plan.situation({},now),"normal")


class SolarPreconditioningTests(unittest.TestCase):
    def test_surplus_heats_outside_gas_schedule_without_raising_trvs(self):
        room = PlanTests().room()
        now = NOW.replace(hour=12)
        r = plan.room_plan({"season": "winter"}, room, now, 19, True, True)
        self.assertFalse(r["occupied"])
        self.assertEqual(r["room_target"], 17)
        self.assertEqual(r["radiator_target"], 17)
        self.assertEqual(r["heat_pump_mode"], "heat")
        self.assertEqual(r["heat_pump_target"], 21)
        self.assertTrue(r["solar_preconditioning"])
        self.assertEqual(plan.room_plan({"season":"winter"},room,now,19,False,True)["heat_pump_mode"],"off")

    def test_surplus_cooling_outside_schedule_respects_comfort_and_holiday(self):
        room = PlanTests().room()
        now = NOW.replace(hour=12)
        self.assertEqual(plan.room_plan({"season":"summer"},room,now,28,True,True)["heat_pump_mode"],"cool")
        self.assertEqual(plan.room_plan({"season":"winter"},room,now,22.5,True,True)["heat_pump_mode"],"off")
        self.assertEqual(plan.room_plan({"season":"winter","exception":{"mode":"holiday","persistent":True}},room,now,19,True,True)["heat_pump_mode"],"off")
        self.assertEqual(plan.room_plan({"season":"winter"},room,now,19,True,False)["heat_pump_mode"],"off")


class SolarStorageTests(unittest.TestCase):
    def test_twenty_degree_floor_and_explicit_ideal_plus_margin(self):
        room={**PlanTests().room(),'solar_storage_available':True,'solar_storage_margin_c':2,
              'cooling_temperature':22,'solar_storage_cooling_margin_c':3}
        house={'season':'winter','solar_storage_cool_min_c':20}
        r=plan.room_plan(house,room,NOW.replace(hour=12),20,True,True)
        self.assertEqual((r['solar_heating_ideal_c'],r['solar_heating_target_c']),(21,23))
        self.assertEqual(r['heat_pump_target'],23)
        house['season']='summer'
        r=plan.room_plan(house,room,NOW.replace(hour=12),25,True,True)
        self.assertEqual((r['solar_cooling_ideal_c'],r['solar_cooling_target_c']),(22,20))
        self.assertEqual(r['heat_pump_target'],20)
        house['solar_storage_cool_min_c']=18
        self.assertEqual(plan.room_plan(house,room,NOW.replace(hour=12),25,True,True)['heat_pump_target'],20)

    def test_room_margins_replace_global_margins_only_for_solar_storage(self):
        room={**PlanTests().room(),'solar_storage_available':True,
              'solar_storage_margin_c':1,'solar_storage_cooling_margin_c':.5}
        house={'season':'winter','solar_storage_margin_c':5,'solar_storage_cooling_margin_c':2}
        r=plan.room_plan(house,room,NOW.replace(hour=12),20,True,True)
        self.assertEqual(r['heat_pump_target'],22)
        self.assertEqual(r['radiator_target'],17)
        room['solar_storage_margin_c']=4
        self.assertEqual(plan.room_plan(house,room,NOW.replace(hour=12),20,True,True)['heat_pump_target'],25)
        house['season']='summer'
        room['cooling_temperature']=26
        self.assertEqual(plan.room_plan(house,room,NOW.replace(hour=12),28,True,True)['heat_pump_target'],25.5)
        self.assertEqual(plan.room_plan(house,room,NOW.replace(hour=12),28,False,True)['heat_pump_mode'],'off')
    def test_large_surplus_expands_temperature_band_but_leaves_gas_target(self):
        room = {**PlanTests().room(), "solar_storage_available": True}
        now = NOW.replace(hour=12)
        r = plan.room_plan({"season":"winter"},room,now,22,True,True)
        self.assertEqual(r["heat_pump_mode"],"heat")
        self.assertEqual(r["heat_pump_target"],26)
        self.assertEqual(r["radiator_target"],17)
        room["comfort_temperature"] = 22
        self.assertEqual(plan.room_plan({"season":"winter"},room,now,23,True,True)["heat_pump_target"],27)
        room["comfort_temperature"] = 21
        cool = plan.room_plan({"season":"summer"},room,now,25,True,True)
        self.assertEqual(cool["heat_pump_target"],24)
        self.assertFalse(plan.room_plan({"season":"winter"},room,now,26,True,True)["heat_pump_mode"] != "off")


class SurplusStabilityTests(unittest.IsolatedAsyncioTestCase):
    setup_control=ControlTests.setup_control

    async def test_ten_continuous_minutes_required_and_a_dip_resets_start(self):
        c,_,_,data,_=self.setup_control()
        c._house_surplus_delays={}
        for minutes in range(10):
            allowed,reason=await c._house_stable_surplus('climate.hp',True,False,NOW+timedelta(minutes=minutes),data)
            self.assertFalse(allowed)
        self.assertTrue((await c._house_stable_surplus('climate.hp',True,False,NOW+timedelta(minutes=10),data))[0])
        await c._house_stable_surplus('climate.hp',False,False,NOW+timedelta(minutes=11),data)
        self.assertFalse((await c._house_stable_surplus('climate.hp',True,False,NOW+timedelta(minutes=12),data))[0])

    async def test_deficit_tolerance_expires_after_ten_minutes_across_restart(self):
        c,_,_,data,_=self.setup_control()
        c._house_surplus_delays={}
        data.update(grid_import_w=100,battery_discharge_w=200,battery_soc=60)
        self.assertTrue((await c._house_stable_surplus('climate.hp',False,True,NOW,data))[0])
        saved=c._house_store.async_save.call_args.args[0]
        other,_,_,_,_=self.setup_control()
        other._house_surplus_delays={}
        other._house_surplus_state=saved['surplus_state']
        self.assertTrue((await other._house_stable_surplus('climate.hp',False,True,NOW+timedelta(minutes=9),data))[0])
        self.assertFalse((await other._house_stable_surplus('climate.hp',False,True,NOW+timedelta(minutes=10),data))[0])

    async def test_large_deficit_low_soc_or_electrical_alarm_is_not_held(self):
        for extra in ({'grid_import_w':400},{'battery_discharge_w':600,'battery_soc':80},
                      {'battery_discharge_w':200,'battery_soc':34},{'inverter_warning':True}):
            c,_,_,data,_=self.setup_control()
            data.update(extra)
            self.assertFalse((await c._house_stable_surplus('climate.hp',False,True,NOW,data))[0])

    async def test_missing_observations_do_not_count_as_confirmed_surplus(self):
        c,_,_,data,_=self.setup_control()
        c._house_surplus_delays={}
        await c._house_stable_surplus('climate.hp',True,False,NOW,data)
        self.assertFalse((await c._house_stable_surplus('climate.hp',True,False,NOW+timedelta(minutes=10),data))[0])

class SolarStorageControlTests(unittest.IsolatedAsyncioTestCase):
    setup_control = ControlTests.setup_control

    async def test_inertia_guard_uses_hp_storage_target_not_gas_maintenance(self):
        c, house, room, data, states = self.setup_control()
        house.update(enabled=True, hydraulics_confirmed=False)
        now = NOW.replace(hour=12)
        states["climate.hp"].last_changed = now - timedelta(hours=2)
        data["grid_export_w"] = 3500
        await c._async_house_plan(data, now)
        decision = data["house_climate_rooms"][0]
        self.assertEqual(decision["room_target"], 17)
        self.assertEqual(decision["heat_pump_target"], 26)
        self.assertTrue(decision["solar_storage_active"])
        self.assertTrue(any(call.args[2].get("hvac_mode") == "heat" and call.args[2].get("temperature") == 26 for call in c.hass.services.async_call.call_args_list))

    async def test_runtime_settings_change_target_without_code_change(self):
        room = {**PlanTests().room(), "solar_storage_available": True}
        house = {"season":"winter", "solar_storage_margin_c":1, "solar_storage_heat_max_c":24}
        first = plan.room_plan(house,room,NOW.replace(hour=12),21,True,True)
        self.assertEqual(first["heat_pump_target"],22)
        house["solar_storage_margin_c"] = 2
        self.assertEqual(plan.room_plan(house,room,NOW.replace(hour=12),21,True,True)["heat_pump_target"],23)

class LowPrioritySolarRoomsTests(unittest.TestCase):
    def test_rooms_without_routine_still_harvest_surplus(self):
        room={**PlanTests().room(), 'manual_only':True, 'solar_storage_available':True}
        for season,current,mode in [('winter',20,'heat'),('summer',29,'cool')]:
            with self.subTest(season=season):
                result=plan.room_plan({'season':season},room,NOW,current,True,True)
                self.assertEqual(result['heat_pump_mode'],mode)
                self.assertFalse(result['occupied'])
                self.assertTrue(result['solar_storage_active'])
        self.assertEqual(plan.room_plan({'season':'winter'},room,NOW,20,False,True)['heat_pump_mode'],'off')
        self.assertEqual(plan.room_plan({'season':'winter'},room,NOW,20,True,False)['heat_pump_mode'],'off')
        self.assertEqual(plan.room_plan({'season':'winter','exception':{'mode':'holiday','persistent':True}},room,NOW,20,True,True)['heat_pump_mode'],'off')

class FiveDegreeStorageTests(unittest.TestCase):
    def test_heat_five_degrees_relative_to_each_room_and_global_cap(self):
        house={'season':'winter','solar_storage_margin_c':5,'solar_storage_heat_max_c':27}
        for normal,expected in [(21,26),(22,27),(20.5,25.5)]:
            room={**PlanTests().room(),'comfort_temperature':normal,'solar_storage_available':True}
            result=plan.room_plan(house,room,NOW,23,True,True)
            self.assertEqual(result['heat_pump_target'],expected)
            result=plan.room_plan(house,room,NOW,expected,True,True)
            self.assertEqual(result['heat_pump_mode'],'off')
    def test_heat_margin_does_not_expand_cooling_margin(self):
        house={'season':'summer','solar_storage_margin_c':5,'solar_storage_cooling_margin_c':2,'solar_storage_cool_min_c':22}
        room={**PlanTests().room(),'solar_storage_available':True}
        self.assertEqual(plan.room_plan(house,room,NOW,29,True,True)['heat_pump_target'],24)

class StorageFanTests(unittest.IsolatedAsyncioTestCase):
    setup_control=ControlTests.setup_control
    async def test_high_fan_restores_previous_and_survives_saved_record(self):
        c,house,room,data,states=self.setup_control()
        states['climate.hp'].state='heat'
        states['climate.hp'].attributes.update(temperature=26,fan_modes=['auto','high','low'],fan_mode='low')
        c._house_owned['climate.hp']={'mode':'heat','target':26,'at':NOW.timestamp()-600,'mode_at':NOW.timestamp()-600}
        self.assertTrue(await c._house_storage_fan('climate.hp',True,NOW))
        record=c._house_owned['climate.hp']
        self.assertEqual(record['fan_previous'],'low')
        self.assertEqual(record['mode_at'],NOW.timestamp()-600)
        states['climate.hp'].attributes['fan_mode']='high'
        c._house_owned['climate.hp']=dict(record)
        self.assertTrue(await c._house_storage_fan('climate.hp',False,NOW+timedelta(minutes=5)))
        self.assertEqual(c._house_owned['climate.hp']['fan'],'low')
        self.assertNotIn('fan_previous',c._house_owned['climate.hp'])
    async def test_manual_fan_and_unowned_compressor_are_untouched(self):
        c,house,room,data,states=self.setup_control()
        states['climate.hp'].state='heat'
        states['climate.hp'].attributes.update(temperature=26,fan_modes=['auto','high','low'],fan_mode='low')
        self.assertFalse(await c._house_storage_fan('climate.hp',True,NOW))
        c._house_owned['climate.hp']={'mode':'heat','target':26,'at':NOW.timestamp()-600,'fan':'high','fan_previous':'auto'}
        self.assertFalse(await c._house_storage_fan('climate.hp',False,NOW))
        c.hass.services.async_call.assert_not_called()

class MideaCommandSequenceTests(unittest.IsolatedAsyncioTestCase):
    setup_control=ControlTests.setup_control
    async def test_start_then_confirm_before_full_state_fan_write(self):
        c,house,room,data,states=self.setup_control()
        state=states['climate.hp']
        state.attributes.update(fan_modes=['auto','high'],fan_mode='auto',temperature=24)
        self.assertTrue(await c._house_command('climate.hp','heat',26,NOW,compressor=True))
        calls=c.hass.services.async_call.call_args_list
        self.assertEqual([call.args[1] for call in calls],['set_hvac_mode','set_temperature'])
        self.assertFalse(await c._house_storage_fan('climate.hp',True,NOW))
        self.assertFalse(await c._house_storage_fan('climate.hp',True,NOW+timedelta(seconds=90)))
        state.state='heat';state.attributes['temperature']=24
        self.assertFalse(await c._house_storage_fan('climate.hp',True,NOW+timedelta(seconds=90)))
        state.attributes['temperature']=26
        self.assertTrue(await c._house_storage_fan('climate.hp',True,NOW+timedelta(seconds=90)))
        self.assertEqual(c.hass.services.async_call.call_args.args[1],'set_fan_mode')
        self.assertEqual(c._house_owned['climate.hp']['mode_at'],NOW.timestamp())

class MideaFanRestoreSequenceTests(unittest.IsolatedAsyncioTestCase):
    setup_control=ControlTests.setup_control
    async def test_restore_waits_for_off_ack_to_avoid_turning_unit_back_on(self):
        c,house,room,data,states=self.setup_control()
        state=states['climate.hp'];state.state='heat'
        state.attributes.update(temperature=26,fan_modes=['auto','high'],fan_mode='high')
        c._house_owned['climate.hp']={'mode':'off','target':None,'at':NOW.timestamp(),'mode_at':NOW.timestamp(),'fan':'high','fan_previous':'auto'}
        self.assertFalse(await c._house_storage_fan('climate.hp',False,NOW))
        self.assertFalse(await c._house_storage_fan('climate.hp',False,NOW+timedelta(seconds=90)))
        state.state='off'
        self.assertTrue(await c._house_storage_fan('climate.hp',False,NOW+timedelta(seconds=90)))
        self.assertEqual(c.hass.services.async_call.call_args.args[2]['fan_mode'],'auto')

class SharedExternalPowerTests(unittest.IsolatedAsyncioTestCase):
    setup_control=ControlTests.setup_control
    async def test_p1_starts_eligible_heads_in_same_cycle_with_one_group_budget(self):
        c,house,room,data,states=self.setup_control()
        house.update(enabled=True,hydraulics_confirmed=False,season='winter')
        room.update(machine='p1',nominal_power_w=3120,power_entity='sensor.p1')
        members=[]
        template=states['climate.hp']
        for i in range(3):
            entity='climate.head'+str(i)
            states[entity]=SimpleNamespace(state='off',last_changed=template.last_changed,attributes=dict(template.attributes))
            members.append(SimpleNamespace(data={**room,'heat_pump_entity':entity},subentry_id=str(i)))
        c._house_config=lambda:(house,members)
        c._house_engine_result={'machine_models':{'p1':{'heat:3':{'powers':[800]*20,'updated_at':NOW.timestamp()}}}}
        c._house_engine_last_at=NOW.timestamp()
        data.update(grid_export_w=2200,phase_l1_headroom_w=3078)
        await c._async_house_plan(data,NOW)
        starts=[call.args[2]['entity_id'] for call in c.hass.services.async_call.call_args_list
                if call.args[1]=='set_hvac_mode' and call.args[2].get('hvac_mode')=='heat']
        self.assertEqual(starts,['climate.head0','climate.head1','climate.head2'],str(data.get('house_climate_rooms')))
        self.assertEqual([r['incremental_power_w'] for r in data['house_climate_rooms']],[1230,0,0])
    async def test_shared_sensor_and_recent_mean_do_not_multiply_by_heads(self):
        c,house,room,data,states=self.setup_control()
        room.update(machine='p1',nominal_power_w=3120,power_entity='sensor.p1')
        members=[SimpleNamespace(data={**room,'heat_pump_entity':'climate.hp'+str(i)}) for i in range(3)]
        for r in members:states[r.data['heat_pump_entity']]=SimpleNamespace(state='off')
        c._house_engine_result={'machine_models':{'p1':{'3':{'powers':[800]*20,'updated_at':NOW.timestamp()}}}}
        average,start,basis=c._house_group_power_estimate(members[0].data,members,NOW)
        self.assertEqual(average,800);self.assertEqual(start,1230)
        states['climate.hp0'].state='heat'
        self.assertEqual(c._house_group_power_estimate(members[1].data,members,NOW)[1],1230)
        c._house_engine_result['machine_models']['p1']['3']['updated_at']=(NOW-timedelta(days=8)).timestamp()
        self.assertEqual(c._house_group_power_estimate(members[1].data,members,NOW)[1],3120)

    async def test_learned_group_start_fits_phase_where_full_nominal_did_not(self):
        c,house,room,data,states=self.setup_control()
        house.update(enabled=True,hydraulics_confirmed=False)
        room.update(machine='p1',nominal_power_w=3120)
        c._house_engine_result={'machine_models':{'p1':{'heat:3':{'powers':[800]*20,'updated_at':NOW.timestamp()}}}}
        c._house_engine_last_at=NOW.timestamp()
        c._house_engine_last_at=NOW.timestamp()
        data.update(grid_export_w=2200,phase_l1_headroom_w=3078)
        await c._async_house_plan(data,NOW)
        r=data['house_climate_rooms'][0]
        self.assertEqual(r['group_start_budget_w'],1230)
        self.assertEqual(r['heat_pump_mode'],'heat')
        self.assertTrue(any(call.args[1]=='set_hvac_mode' for call in c.hass.services.async_call.call_args_list))

    async def test_small_shared_grid_allowance_uses_mean_but_phase_guard_keeps_start_margin(self):
        c,house,room,data,states=self.setup_control()
        house.update(enabled=True,hydraulics_confirmed=False)
        room.update(machine='p1',nominal_power_w=3120)
        c._house_engine_result={'machine_models':{'p1':{'heat:3':{'powers':[800]*20,'updated_at':NOW.timestamp()}}}}
        c._house_engine_last_at=NOW.timestamp()
        states["input_number.energy_manager_supporto_rete_clima"]=SimpleNamespace(state="300")
        data.update(grid_export_w=720,phase_l1_headroom_w=1300)
        await c._async_house_plan(data,NOW)
        self.assertEqual(data['house_climate_rooms'][0]['heat_pump_mode'],'heat')
        c2,h2,r2,d2,s2=self.setup_control();h2.update(enabled=True,hydraulics_confirmed=False);r2.update(machine='p1',nominal_power_w=3120)
        s2["input_number.energy_manager_supporto_rete_clima"]=SimpleNamespace(state="300")
        c2._house_engine_result=c._house_engine_result;c2._house_engine_last_at=NOW.timestamp();d2.update(grid_export_w=720,phase_l1_headroom_w=1000)
        await c2._async_house_plan(d2,NOW)
        self.assertEqual(d2['house_climate_rooms'][0]['heat_pump_mode'],'off')

class TRVReferenceTests(unittest.IsolatedAsyncioTestCase):
    setup_control=ControlTests.setup_control
    async def test_cold_trv_offset_and_native_limit_preserve_room_goal(self):
        c,house,room,data,states=self.setup_control()
        room['name']='Camera ES'
        states['input_boolean.energy_manager_riferimento_testine_camera_es']=SimpleNamespace(state='on')
        states['climate.hp'].attributes['current_temperature']=29
        states['climate.valve'].attributes['current_temperature']=21.5
        offset,quality=await c._house_trv_reference(house,room,NOW)
        self.assertEqual(offset,7.5);self.assertEqual(quality,'trvs_radiators_cold')
        self.assertEqual(c._house_calibrated_setpoint('climate.hp',25.5,'heat_pump',{},'heat',27,24,reference_offset=offset),28)
        states['climate.gas'].state='heat';states['climate.valve'].attributes.update(hvac_action='heating',current_temperature=25)
        offset,quality=await c._house_trv_reference(house,room,NOW+timedelta(minutes=10))
        self.assertEqual(offset,7.5);self.assertEqual(quality,'frozen_cold_offset_radiators_hot')
        self.assertEqual(c._house_reference_offsets['climate.hp']['offset'],7.5)

    async def test_hot_radiators_without_cold_model_do_not_invent_offset(self):
        c,house,room,data,states=self.setup_control();room['name']='Camera ES'
        states['input_boolean.energy_manager_riferimento_testine_camera_es']=SimpleNamespace(state='on')
        states['climate.hp'].attributes['current_temperature']=29
        states['climate.gas'].state='heat';states['climate.valve'].attributes['hvac_action']='heating'
        offset,quality=await c._house_trv_reference(house,room,NOW)
        self.assertIsNone(offset);self.assertEqual(quality,'radiators_hot_no_valid_cold_offset')
    async def test_confirmed_reference_requires_fresh_values(self):
        c,house,room,data,states=self.setup_control();room['name']='Camera ES'
        states['input_boolean.energy_manager_riferimento_testine_camera_es']=SimpleNamespace(state='on')
        states['climate.valve'].last_changed=NOW-timedelta(hours=7)
        offset,quality=await c._house_trv_reference(house,room,NOW)
        self.assertIsNone(offset);self.assertEqual(quality,'reference_unavailable_or_stale')

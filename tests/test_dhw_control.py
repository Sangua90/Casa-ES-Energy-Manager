"""Execute the actual control class with a minimal HA service/state boundary."""
import ast
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from test_dhw_history_plan import ROOT, dhw, NOW

source = (ROOT / "custom_components/casa_es_energy_manager/coordinator_v1520.py").read_text()
tree = ast.parse(source)
classes = ast.Module(body=[n for n in tree.body if isinstance(n, ast.ClassDef)], type_ignores=[])
namespace = {"PreviousCoordinator": object, "Any": object, "number": dhw.number,
             "temperature": dhw.temperature, "plan": dhw.plan, "forecast": dhw.forecast,
             "timedelta": timedelta}
for name, value in {"CONF_THERMAL_BASE_TEMP_C": "thermal_base_temperature_c",
                    "CONF_THERMAL_NORMAL_MAX_TEMP_C": "thermal_normal_max_temperature_c",
                    "CONF_THERMAL_HARD_MAX_TEMP_C": "thermal_hard_max_temperature_c",
                    "CONF_THERMAL_BOOST_ENTITY": "thermal_boost_entity",
                    "CONF_THERMAL_HEATING_ENTITY": "thermal_heating_entity",
                    "CONF_THERMAL_LEGIONELLA_ENTITY": "thermal_legionella_entity"}.items():
    namespace[name] = value
exec(compile(classes, "coordinator_v1520.py", "exec"), namespace)
Coordinator = namespace["CasaESEnergyCoordinator"]


class ControlTests(unittest.IsolatedAsyncioTestCase):
    def setup_control(self):
        c = object.__new__(Coordinator)
        c.real_control_enabled = True
        c._dhw_models, c._dhw_plans = {}, {}
        c._thermal_boost_owned, c._thermal_target_c = set(), {}
        c._dhw_boost_last_stop = {}
        c._thermal_context = lambda x: x
        c._virtual_surplus_opportunity = lambda _: False
        c._battery_allocation = lambda _: {"below_target": False}
        c._set_water_temperature = AsyncMock()
        c._set_boost = AsyncMock()
        c._stop_owned_thermal_boost = AsyncMock()
        states = {"water_heater.test": SimpleNamespace(state="GREEN", attributes={"max_temp": 70, "operation_list": ["GREEN", "BOOST"]}),
                  "switch.boost": SimpleNamespace(state="off"),
                  "binary_sensor.heat": SimpleNamespace(state="on"),
                  "binary_sensor.legionella": SimpleNamespace(state="off")}
        c.hass = SimpleNamespace(states=SimpleNamespace(get=states.get),
                                 config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°C")),
                                 services=SimpleNamespace(async_call=AsyncMock()))
        item = {"subentry_id": "boiler", "entity_id": "water_heater.test", "device_type": "thermal_storage",
                "thermal_boost_entity": "switch.boost", "thermal_heating_entity": "binary_sensor.heat",
                "thermal_legionella_entity": "binary_sensor.legionella", "thermal_current_temperature_c": 43,
                "thermal_target_temperature_c": 50, "nominal_power_w": 1200, "phase": "l1", "min_battery_soc": 40}
        data = {"managed_device_configs": [item], "battery_soc": 80, "grid_export_w": 1500,
                "phase_l1_headroom_w": 2000, "inverter_headroom_w": 4000}
        return c, item, data, states

    async def test_surplus_boost_can_recover_below_base(self):
        c, _, data, _ = self.setup_control()
        self.assertTrue(await c._async_apply_thermal_control(data, NOW.replace(hour=17)))
        c._set_boost.assert_awaited_once_with("switch.boost", True)
        self.assertIn("boiler", c._thermal_boost_owned)

    async def test_no_surplus_only_early_green(self):
        c, _, data, _ = self.setup_control()
        data["grid_export_w"] = 0
        self.assertTrue(await c._async_apply_thermal_control(data, NOW.replace(hour=17)))
        c._set_boost.assert_not_awaited()
        c._set_water_temperature.assert_awaited_once_with("water_heater.test", 53)

    async def test_battery_or_grid_supply_does_not_keep_boost_running(self):
        c, item, data, _ = self.setup_control()
        c._thermal_boost_owned.add("boiler")
        item.update(thermal_boost_active=True, current_power_w=1200)
        data.update(grid_export_w=0, battery_discharge_w=800)
        await c._async_apply_thermal_control(data, NOW)
        c._stop_owned_thermal_boost.assert_awaited_once()

    async def test_manual_boost_and_legionella_untouched(self):
        for field in ("thermal_boost_active", "thermal_legionella_active"):
            c, item, data, _ = self.setup_control()
            item[field] = True
            self.assertFalse(await c._async_apply_thermal_control(data, NOW))
            c._set_boost.assert_not_awaited()
            c._set_water_temperature.assert_not_awaited()

    async def test_phase_headroom_blocks_boost(self):
        c, _, data, _ = self.setup_control()
        data["phase_l1_headroom_w"] = 500
        await c._async_apply_thermal_control(data, NOW.replace(hour=17))
        c._set_boost.assert_not_awaited()

    async def test_missing_status_is_not_off(self):
        c, _, data, states = self.setup_control()
        states["binary_sensor.legionella"].state = "unavailable"
        self.assertFalse(await c._async_apply_thermal_control(data, NOW))
        c._set_boost.assert_not_awaited()

    async def test_recent_stop_does_not_chatter(self):
        c, _, data, _ = self.setup_control()
        c._dhw_boost_last_stop["boiler"] = NOW - timedelta(minutes=1)
        await c._async_apply_thermal_control(data, NOW)
        c._set_boost.assert_not_awaited()

    async def test_main_and_power_switch_never_off(self):
        c, _, _, _ = self.setup_control()
        c._thermal_main_entity_commands_blocked = 0
        await c._async_call_entity_control("water_heater.test", False)
        await c._async_call_entity_control("switch.ariston_power", False)
        self.assertEqual(c._thermal_main_entity_commands_blocked, 2)
        c.hass.services.async_call.assert_not_awaited()

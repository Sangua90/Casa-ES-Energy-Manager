"""Regression tests for v1.5.14 DHW thermal learning."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "casa_es_energy_manager"
PACKAGE = "casa_es_thermal_test"

pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = pkg

ha = types.ModuleType("homeassistant")
ha.__path__ = []
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
ha_storage = types.ModuleType("homeassistant.helpers.storage")
ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
ha_dt = types.ModuleType("homeassistant.util.dt")


class Store:
    def __init__(self, *_args, **_kwargs):
        self.value = None

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.value = value


_clock = {"now": datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)}
ha_dt.now = lambda: _clock["now"]
ha_dt.parse_date = lambda value: datetime.fromisoformat(str(value)).date()
ha_storage.Store = Store
ha_helpers.storage = ha_storage
ha_util.dt = ha_dt
ha.helpers = ha_helpers
ha.util = ha_util
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.helpers"] = ha_helpers
sys.modules["homeassistant.helpers.storage"] = ha_storage
sys.modules["homeassistant.util"] = ha_util
sys.modules["homeassistant.util.dt"] = ha_dt

spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.thermal_learning_v15", ROOT / "thermal_learning_v15.py"
)
assert spec and spec.loader
thermal = importlib.util.module_from_spec(spec)
sys.modules[f"{PACKAGE}.thermal_learning_v15"] = thermal
spec.loader.exec_module(thermal)


class ThermalLearningV1514Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _clock["now"] = datetime(2026, 9, 2, 7, 0, tzinfo=timezone.utc)
        self.learner = thermal.ThermalLearnerV15(object(), "test")

    @staticmethod
    def device(temp: float, *, heating: bool = False, boost: bool = False):
        return {
            "subentry_id": "boiler",
            "device_type": "thermal_storage",
            "management_mode": "auto",
            "learning_excluded": False,
            "thermal_current_temperature_c": temp,
            "thermal_heating": heating,
            "thermal_boost_active": boost,
            "current_power_w": 0.0,
        }

    async def test_five_second_refreshes_do_not_erase_temperature_baseline(self):
        await self.learner.async_observe([self.device(55.0)])
        baseline = self.learner._last["boiler"]["at"]

        for seconds in (5, 10, 15, 20, 25):
            _clock["now"] = baseline + timedelta(seconds=seconds)
            await self.learner.async_observe([self.device(55.0)])

        self.assertEqual(self.learner._last["boiler"]["at"], baseline)

        _clock["now"] = baseline + timedelta(minutes=5)
        await self.learner.async_observe([self.device(54.0)])

        profile = self.learner.profile("boiler")
        self.assertEqual(profile["recent_7d_days"], 1)
        self.assertEqual(profile["draw_events"], 1)
        self.assertAlmostEqual(profile["last_draw_drop_c"], 1.0)

    async def test_negative_temperature_change_while_heat_pump_runs_is_a_draw(self):
        await self.learner.async_observe([self.device(53.0, heating=True)])
        _clock["now"] += timedelta(minutes=10)
        await self.learner.async_observe([self.device(52.0, heating=True)])

        profile = self.learner.profile("boiler")
        self.assertEqual(profile["draw_events"], 1)
        self.assertEqual(profile["draw_events_while_heating"], 1)
        self.assertTrue(profile["last_draw_while_heating"])

    async def test_slow_idle_drop_is_standby_loss_not_draw(self):
        await self.learner.async_observe([self.device(55.0)])
        _clock["now"] += timedelta(hours=1)
        await self.learner.async_observe([self.device(54.0)])

        profile = self.learner.profile("boiler")
        self.assertEqual(profile.get("draw_events", 0), 0)
        self.assertEqual(profile["passive_loss_samples"], 1)
        self.assertAlmostEqual(profile["standby_loss_c_per_h"], 1.0)

    async def test_operating_state_change_resets_baseline(self):
        await self.learner.async_observe([self.device(55.0, heating=False)])
        old = self.learner._last["boiler"]["at"]
        _clock["now"] = old + timedelta(minutes=5)
        await self.learner.async_observe([self.device(55.0, heating=True)])
        self.assertEqual(self.learner._last["boiler"]["at"], _clock["now"])


if __name__ == "__main__":
    unittest.main()

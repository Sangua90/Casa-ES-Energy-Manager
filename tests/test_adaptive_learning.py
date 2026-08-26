"""Safety tests for adaptive power learning."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "casa_es_energy_manager"
PACKAGE = "casa_es_adaptive_test"

pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = pkg

ha = types.ModuleType("homeassistant")
ha_core = types.ModuleType("homeassistant.core")
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_storage = types.ModuleType("homeassistant.helpers.storage")


class HomeAssistant:
    pass


class Store:
    def __init__(self, *_args, **_kwargs):
        self.value = None

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.value = value


ha_core.HomeAssistant = HomeAssistant
ha_storage.Store = Store
sys.modules["homeassistant"] = ha
sys.modules["homeassistant.core"] = ha_core
sys.modules["homeassistant.helpers"] = ha_helpers
sys.modules["homeassistant.helpers.storage"] = ha_storage


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[f"{PACKAGE}.{name}"] = module
    spec.loader.exec_module(module)
    return module


_load("const", "const.py")
adaptive = _load("adaptive_learning", "adaptive_learning.py")


class AdaptiveLearningTests(unittest.IsolatedAsyncioTestCase):
    def learner(self):
        return adaptive.AdaptivePowerLearner(HomeAssistant(), "test")

    async def test_switch_off_never_creates_active_sample(self):
        learner = self.learner()
        await learner.async_observe(
            [
                {
                    "adaptive_power_profile": True,
                    "power_sensor": "sensor.test_power",
                    "entity_id": "switch.test",
                    "state": "off",
                    "current_power_w": 800,
                }
            ]
        )
        profile = learner.profile_for("switch.test", "general", 1000)
        self.assertEqual(profile["active_samples"], 0)
        self.assertEqual(profile["estimated_power_w"], 1000)

    async def test_switch_on_learns_real_active_power(self):
        learner = self.learner()
        await learner.async_observe(
            [
                {
                    "adaptive_power_profile": True,
                    "power_sensor": "sensor.test_power",
                    "entity_id": "switch.test",
                    "state": "on",
                    "current_power_w": 800,
                }
            ]
        )
        profile = learner.profile_for("switch.test", "general", 1000)
        self.assertEqual(profile["active_samples"], 1)
        self.assertEqual(profile["mean_w"], 800)
        self.assertEqual(profile["estimated_power_w"], 1000)

    async def test_shared_power_sensor_is_not_learned(self):
        learner = self.learner()
        await learner.async_observe(
            [
                {
                    "adaptive_power_profile": True,
                    "power_sensor": "sensor.shared",
                    "adaptive_shared_power_sensor": True,
                    "entity_id": "switch.test",
                    "state": "on",
                    "current_power_w": 2500,
                }
            ]
        )
        profile = learner.admission_profile_for(
            {
                "entity_id": "switch.test",
                "adaptive_shared_power_sensor": True,
            },
            1200,
        )
        self.assertEqual(profile["status"], "shared_power_sensor")
        self.assertEqual(profile["estimated_power_w"], 1200)
        self.assertNotIn("switch.test", learner.data["devices"])

    def test_climate_off_profile_is_never_used_for_next_start(self):
        learner = self.learner()
        learner.data = {
            "schema_version": adaptive.PROFILE_SCHEMA_VERSION,
            "devices": {
                "climate.test": {
                    "modes": {
                        "off": {
                            "samples": 100,
                            "active_samples": 30,
                            "mean_w": 60,
                            "m2": 0,
                            "min_w": 50,
                            "max_w": 100,
                            "last_power_w": 0,
                            "last_action": "off",
                        },
                        "cool": {
                            "samples": 3,
                            "active_samples": 3,
                            "mean_w": 180,
                            "m2": 0,
                            "min_w": 170,
                            "max_w": 190,
                            "last_power_w": 180,
                            "last_action": "cooling",
                        },
                    }
                }
            },
        }
        profile = learner.admission_profile_for(
            {
                "entity_id": "climate.test",
                "state": "off",
                "hvac_mode": "off",
            },
            1200,
        )
        self.assertEqual(profile["status"], "learning")
        self.assertEqual(profile["source_mode"], "cool")
        self.assertEqual(profile["estimated_power_w"], 1200)

    async def test_old_profile_schema_is_reset_on_load(self):
        learner = self.learner()
        learner.store.value = {
            "devices": {
                "climate.test": {
                    "modes": {
                        "off": {"active_samples": 99, "mean_w": 50}
                    }
                }
            }
        }
        await learner.async_load()
        self.assertEqual(learner.data["schema_version"], adaptive.PROFILE_SCHEMA_VERSION)
        self.assertEqual(learner.data["devices"], {})


if __name__ == "__main__":
    unittest.main()

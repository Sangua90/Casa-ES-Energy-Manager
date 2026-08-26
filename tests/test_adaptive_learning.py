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


const = _load("const", "const.py")
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

    async def test_switch_climate_learns_into_current_hvac_mode(self):
        """A grouped switch such as Clima P1 must learn cool/heat separately."""
        learner = self.learner()
        device = {
            "adaptive_power_profile": True,
            "power_sensor": "sensor.p1_power",
            "entity_id": "switch.clima_p1",
            "state": "on",
            "current_power_w": 1500,
            "device_type": const.DEVICE_TYPE_CLIMATE,
            "mode_climate_entity": "climate.p1_reference",
            "profile_mode": "cool",
            "profile_hvac_action": "cooling",
            "mode_reference_required": True,
            "mode_reference_available": True,
        }
        await learner.async_observe([device])

        modes = learner.data["devices"]["switch.clima_p1"]["modes"]
        self.assertIn("cool", modes)
        self.assertNotIn("general", modes)
        self.assertEqual(modes["cool"]["active_samples"], 1)
        self.assertEqual(modes["cool"]["mean_w"], 1500)

    async def test_switch_climate_separates_cool_and_heat_profiles(self):
        learner = self.learner()
        base = {
            "adaptive_power_profile": True,
            "power_sensor": "sensor.p1_power",
            "entity_id": "switch.clima_p1",
            "state": "on",
            "device_type": const.DEVICE_TYPE_CLIMATE,
            "mode_climate_entity": "climate.p1_reference",
            "mode_reference_required": True,
            "mode_reference_available": True,
        }
        await learner.async_observe(
            [{**base, "profile_mode": "cool", "current_power_w": 1400}]
        )
        await learner.async_observe(
            [{**base, "profile_mode": "heat", "current_power_w": 2200}]
        )
        modes = learner.data["devices"]["switch.clima_p1"]["modes"]
        self.assertEqual(modes["cool"]["mean_w"], 1400)
        self.assertEqual(modes["heat"]["mean_w"], 2200)

    async def test_unavailable_climate_reference_does_not_pollute_learning(self):
        learner = self.learner()
        await learner.async_observe(
            [
                {
                    "adaptive_power_profile": True,
                    "power_sensor": "sensor.p1_power",
                    "entity_id": "switch.clima_p1",
                    "state": "on",
                    "current_power_w": 1600,
                    "device_type": const.DEVICE_TYPE_CLIMATE,
                    "mode_reference_required": True,
                    "mode_reference_available": False,
                }
            ]
        )
        self.assertNotIn("switch.clima_p1", learner.data["devices"])

    def test_mature_general_profile_bridges_new_switch_climate_mode(self):
        """Existing v1.1.1 switch learning remains useful after reconfiguration."""
        learner = self.learner()
        learner.data = {
            "schema_version": adaptive.PROFILE_SCHEMA_VERSION,
            "devices": {
                "switch.clima_p1": {
                    "modes": {
                        "general": {
                            "samples": 100,
                            "active_samples": 100,
                            "mean_w": 1400,
                            "m2": 100 * 100,
                            "min_w": 1000,
                            "max_w": 1900,
                            "last_power_w": 1400,
                            "last_action": "on",
                        }
                    }
                }
            },
        }
        profile = learner.admission_profile_for(
            {
                "entity_id": "switch.clima_p1",
                "device_type": const.DEVICE_TYPE_CLIMATE,
                "mode_climate_entity": "climate.p1_reference",
                "profile_mode": "cool",
                "mode_reference_required": True,
                "mode_reference_available": True,
            },
            1000,
        )
        self.assertEqual(profile["status"], "learning")
        self.assertEqual(profile["fallback_mode"], "general")
        self.assertGreater(profile["estimated_power_w"], 1000)

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

    async def test_mature_profile_limits_single_extreme_outlier(self):
        """The dehumidifier-style one-off spike must not dominate forever."""
        learner = self.learner()
        device = {
            "adaptive_power_profile": True,
            "power_sensor": "sensor.dehum_power",
            "entity_id": "switch.dehum",
            "state": "on",
            "current_power_w": 130,
        }
        for _ in range(24):
            await learner.async_observe([device])
        await learner.async_observe([{**device, "current_power_w": 1175}])

        profile = learner.profile_for("switch.dehum", "general", 130)
        self.assertEqual(profile["status"], "ready")
        self.assertTrue(profile["outlier_limited"])
        self.assertLess(profile["estimated_power_w"], 1175)
        self.assertLessEqual(
            profile["estimated_power_w"],
            round(profile["mean_w"] * const.ADAPTIVE_ESTIMATE_MAX_MEAN_FACTOR, 1) + 0.1,
        )

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

    async def test_v111_schema_two_profiles_survive_v120_load(self):
        learner = self.learner()
        stored = {
            "schema_version": adaptive.PROFILE_SCHEMA_VERSION,
            "devices": {
                "switch.clima_p1": {
                    "modes": {
                        "general": {
                            "samples": 297,
                            "active_samples": 297,
                            "mean_w": 1380,
                        }
                    }
                }
            },
        }
        learner.store.value = stored
        await learner.async_load()
        self.assertEqual(learner.data, stored)


if __name__ == "__main__":
    unittest.main()

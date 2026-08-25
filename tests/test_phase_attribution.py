"""Tests for read-only phase attribution."""

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "casa_es_energy_manager"
    / "phase_attribution.py"
)
SPEC = importlib.util.spec_from_file_location("casa_es_phase_attribution", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
phase_attribution = MODULE.phase_attribution


class PhaseAttributionTests(unittest.TestCase):
    def test_monitored_load_explains_but_does_not_add_to_phase_total(self):
        monitored = [
            {
                "name": "Forno",
                "phase": "l1",
                "enabled": True,
                "available": True,
                "current_power_w": 1200,
            }
        ]
        result = phase_attribution(
            monitored,
            [],
            phase_l1_w=1800,
            phase_l2_w=400,
            phase_l3_w=300,
        )
        self.assertEqual(result["phase_known_load_l1_w"], 1200.0)
        self.assertEqual(result["phase_other_load_l1_w"], 600.0)

    def test_three_phase_load_is_split_equally(self):
        monitored = [
            {
                "name": "Carico trifase",
                "phase": "three_phase",
                "enabled": True,
                "available": True,
                "current_power_w": 3000,
            }
        ]
        result = phase_attribution(
            monitored,
            [],
            phase_l1_w=1500,
            phase_l2_w=1500,
            phase_l3_w=1500,
        )
        self.assertEqual(result["phase_known_load_l1_w"], 1000.0)
        self.assertEqual(result["phase_known_load_l2_w"], 1000.0)
        self.assertEqual(result["phase_known_load_l3_w"], 1000.0)

    def test_managed_device_power_sensor_contributes_to_known_load(self):
        managed = [
            {
                "name": "Boiler",
                "phase": "l2",
                "enabled": True,
                "available": True,
                "current_power_w": 700,
            }
        ]
        result = phase_attribution(
            [],
            managed,
            phase_l1_w=100,
            phase_l2_w=900,
            phase_l3_w=100,
        )
        self.assertEqual(result["phase_known_load_l2_w"], 700.0)
        self.assertEqual(result["phase_other_load_l2_w"], 200.0)


if __name__ == "__main__":
    unittest.main()

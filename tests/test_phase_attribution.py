"""Tests for read-only phase attribution."""

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "casa_es_energy_manager"
    / "phase_attribution_math.py"
)
SPEC = importlib.util.spec_from_file_location("casa_es_phase_attribution_math", MODULE_PATH)
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

    def test_shared_spa_meter_is_counted_once_when_heater_is_off(self):
        """Regression for real Casa ES diagnostic: filter ON, heater OFF, same meter."""
        managed = [
            {
                "name": "Filtrazione SPA",
                "entity_id": "switch.spa_filtrazione",
                "state": "on",
                "phase": "l1",
                "enabled": True,
                "available": True,
                "power_sensor": "sensor.giardino_meter_spa_potenza",
                "adaptive_shared_power_sensor": True,
                "current_power_w": 30.3,
            },
            {
                "name": "Riscaldamento SPA",
                "entity_id": "switch.giardino_spa_riscaldamento",
                "state": "off",
                "phase": "l1",
                "enabled": True,
                "available": True,
                "power_sensor": "sensor.giardino_meter_spa_potenza",
                "adaptive_shared_power_sensor": True,
                "current_power_w": 30.3,
            },
        ]
        result = phase_attribution(
            [],
            managed,
            phase_l1_w=200,
            phase_l2_w=0,
            phase_l3_w=0,
        )
        self.assertEqual(result["phase_known_load_l1_w"], 30.3)
        self.assertEqual(result["phase_other_load_l1_w"], 169.7)
        spa = {item["name"]: item for item in result["phase_load_breakdown"]}
        self.assertEqual(spa["Filtrazione SPA"]["power_w"], 30.3)
        self.assertEqual(spa["Riscaldamento SPA"]["power_w"], 0.0)
        self.assertEqual(
            spa["Riscaldamento SPA"]["attribution"], "shared_meter_sibling"
        )

    def test_two_active_children_same_phase_still_count_shared_meter_once(self):
        managed = [
            {
                "name": "A",
                "state": "on",
                "phase": "l1",
                "enabled": True,
                "power_sensor": "sensor.shared",
                "adaptive_shared_power_sensor": True,
                "current_power_w": 2500,
            },
            {
                "name": "B",
                "state": "on",
                "phase": "l1",
                "enabled": True,
                "power_sensor": "sensor.shared",
                "adaptive_shared_power_sensor": True,
                "current_power_w": 2500,
            },
        ]
        result = phase_attribution(
            [], managed, phase_l1_w=3000, phase_l2_w=0, phase_l3_w=0
        )
        self.assertEqual(result["phase_known_load_l1_w"], 2500.0)
        self.assertEqual(result["phase_other_load_l1_w"], 500.0)

    def test_shared_meter_across_different_active_phases_is_not_guessed(self):
        managed = [
            {
                "name": "A",
                "state": "on",
                "phase": "l1",
                "enabled": True,
                "power_sensor": "sensor.shared",
                "adaptive_shared_power_sensor": True,
                "current_power_w": 1000,
            },
            {
                "name": "B",
                "state": "on",
                "phase": "l2",
                "enabled": True,
                "power_sensor": "sensor.shared",
                "adaptive_shared_power_sensor": True,
                "current_power_w": 1000,
            },
        ]
        result = phase_attribution(
            [], managed, phase_l1_w=1200, phase_l2_w=1200, phase_l3_w=0
        )
        self.assertEqual(result["phase_known_load_l1_w"], 0.0)
        self.assertEqual(result["phase_known_load_l2_w"], 0.0)
        self.assertEqual(result["phase_other_load_l1_w"], 1200.0)
        self.assertEqual(result["phase_other_load_l2_w"], 1200.0)


if __name__ == "__main__":
    unittest.main()

"""Pure tests for v1.4 monitored-load emergency shedding helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "casa_es_energy_manager"
PACKAGE = "casa_es_monitored_emergency_test"

pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = pkg


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[f"{PACKAGE}.{name}"] = module
    spec.loader.exec_module(module)
    return module


_load("const", "const.py")
emergency = _load("monitored_emergency", "monitored_emergency.py")


class MonitoredEmergencyTests(unittest.TestCase):
    def load(self, **changes):
        value = {
            "subentry_id": "a",
            "name": "Lavastoviglie",
            "power_sensor": "sensor.dishwasher_power",
            "phase": "l2",
            "enabled": True,
            "available": True,
            "current_power_w": 1200.0,
            "emergency_entity": "button.dishwasher_pause",
        }
        value.update(changes)
        return value

    def test_read_only_load_is_never_eligible(self):
        load = self.load(emergency_entity=None)
        self.assertFalse(emergency.monitored_load_is_drawing(load))
        self.assertEqual(emergency.eligible_emergency_loads([load]), [])

    def test_only_real_active_power_is_eligible(self):
        self.assertTrue(emergency.monitored_load_is_drawing(self.load()))
        self.assertFalse(
            emergency.monitored_load_is_drawing(self.load(current_power_w=7.0))
        )
        self.assertFalse(
            emergency.monitored_load_is_drawing(self.load(available=False))
        )
        self.assertFalse(emergency.monitored_load_is_drawing(self.load(enabled=False)))

    def test_phase_filter_keeps_same_phase_and_three_phase_only(self):
        loads = [
            self.load(subentry_id="l1", phase="l1"),
            self.load(subentry_id="l2", phase="l2"),
            self.load(subentry_id="three", phase="three_phase"),
        ]
        result = emergency.eligible_emergency_loads(loads, phases={"l2"})
        self.assertEqual(
            {item["subentry_id"] for item in result}, {"l2", "three"}
        )

    def test_excluded_already_shed_load_is_ignored(self):
        result = emergency.eligible_emergency_loads(
            [self.load(subentry_id="shed"), self.load(subentry_id="other")],
            excluded_subentry_ids={"shed"},
        )
        self.assertEqual([item["subentry_id"] for item in result], ["other"])

    def test_choose_smallest_single_load_that_solves_required_relief(self):
        loads = [
            self.load(subentry_id="small", current_power_w=300),
            self.load(subentry_id="right", current_power_w=650),
            self.load(subentry_id="big", current_power_w=1800),
        ]
        selected = emergency.choose_relief_candidate(loads, 500)
        self.assertEqual(selected["subentry_id"], "right")

    def test_choose_largest_when_no_single_load_is_enough(self):
        loads = [
            self.load(subentry_id="small", current_power_w=300),
            self.load(subentry_id="big", current_power_w=800),
        ]
        selected = emergency.choose_relief_candidate(loads, 1200)
        self.assertEqual(selected["subentry_id"], "big")

    def test_warning_phases_uses_zero_headroom(self):
        phases = emergency.warning_phases(
            {
                "phase_warning": True,
                "phase_l1_headroom_w": 500,
                "phase_l2_headroom_w": 0,
                "phase_l3_headroom_w": 250,
                "hottest_phase": "l2",
            }
        )
        self.assertEqual(phases, {"l2"})

    def test_warning_phases_falls_back_to_hottest_phase(self):
        phases = emergency.warning_phases(
            {
                "phase_warning": True,
                "phase_l1_headroom_w": None,
                "phase_l2_headroom_w": None,
                "phase_l3_headroom_w": None,
                "hottest_phase": "l3",
            }
        )
        self.assertEqual(phases, {"l3"})

    def test_relief_calculations_use_measured_excess(self):
        data = {
            "grid_import_w": 6100,
            "load_power_w": 9900,
            "phase_l1_power_w": 3200,
            "phase_l2_power_w": 1000,
            "phase_l3_power_w": 1000,
        }
        self.assertEqual(emergency.grid_relief_w(data, 5750), 350)
        self.assertEqual(emergency.inverter_relief_w(data, 9750), 150)
        self.assertEqual(emergency.phase_relief_w(data, "l1", 3050), 150)
        phase, relief = emergency.most_overloaded_phase(
            data, {"l1", "l2"}, 3050
        )
        self.assertEqual(phase, "l1")
        self.assertEqual(relief, 150)


if __name__ == "__main__":
    unittest.main()

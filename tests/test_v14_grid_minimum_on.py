"""Regression test for v1.4 grid-only emergency anti-cycling semantics."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "casa_es_energy_manager"
PACKAGE = "casa_es_v14_grid_test"

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
_load("device_dry_run", "device_dry_run.py")
v1_dry = _load("device_dry_run_v1", "device_dry_run_v1.py")


class GridMinimumOnTests(unittest.TestCase):
    def test_grid_only_warning_does_not_bypass_twenty_minute_minimum_on(self):
        device = {
            "subentry_id": "clima",
            "name": "Clima",
            "entity_id": "climate.test",
            "state": "cool",
            "available": True,
            "current_power_w": 900,
            "nominal_power_w": 1000,
            "admission_power_w": 1000,
            "expected_runtime_minutes": 60,
            "priority": 50,
            "phase": "l2",
            "allow_grid": True,
            "max_grid_power_w": 100,
            "enabled": True,
            "min_battery_soc": 40,
            "seconds_since_change": 60,
            "min_on_minutes": 20,
            "min_off_minutes": 20,
            "management_mode": "auto",
            "battery_discharge_override_w": 0,
            "on_only": False,
            "adaptive_shared_power_sensor": False,
        }
        data = {
            "battery_soc": 80,
            "grid_import_w": 5900,
            "battery_discharge_w": 0,
            "grid_warning": True,
            "phase_warning": False,
            "inverter_warning": False,
            "grid_headroom_w": 0,
            "inverter_headroom_w": 4000,
            "phase_l1_headroom_w": 2000,
            "phase_l2_headroom_w": 1500,
            "phase_l3_headroom_w": 2000,
            "solar_after_house_w": 0,
            "pv_potential_after_house_w": 0,
        }
        policy = {
            "flexible_energy_budget_kwh": 0,
            "protect_grid_required": True,
            "target_reachability": "comfortable",
            "battery_first_preferred": False,
        }
        result = v1_dry.evaluate_managed_devices([device], data=data, policy=policy)
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "minimum_on_protected")
        self.assertFalse(decision["would_stop"])
        self.assertFalse(decision["stop_is_hard_safety"])

    def test_phase_warning_still_bypasses_minimum_on(self):
        device = {
            "subentry_id": "clima",
            "name": "Clima",
            "entity_id": "climate.test",
            "state": "cool",
            "available": True,
            "current_power_w": 900,
            "nominal_power_w": 1000,
            "admission_power_w": 1000,
            "expected_runtime_minutes": 60,
            "priority": 50,
            "phase": "l2",
            "allow_grid": False,
            "max_grid_power_w": 0,
            "enabled": True,
            "min_battery_soc": 40,
            "seconds_since_change": 60,
            "min_on_minutes": 20,
            "min_off_minutes": 20,
            "management_mode": "auto",
            "battery_discharge_override_w": 0,
            "on_only": False,
            "adaptive_shared_power_sensor": False,
        }
        data = {
            "battery_soc": 80,
            "grid_import_w": 0,
            "battery_discharge_w": 0,
            "grid_warning": False,
            "phase_warning": True,
            "inverter_warning": False,
            "grid_headroom_w": 5000,
            "inverter_headroom_w": 4000,
            "phase_l1_headroom_w": 2000,
            "phase_l2_headroom_w": 0,
            "phase_l3_headroom_w": 2000,
            "solar_after_house_w": 0,
            "pv_potential_after_house_w": 0,
        }
        policy = {
            "flexible_energy_budget_kwh": 0,
            "protect_grid_required": False,
            "target_reachability": "comfortable",
            "battery_first_preferred": False,
        }
        result = v1_dry.evaluate_managed_devices([device], data=data, policy=policy)
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "safety_stop")
        self.assertTrue(decision["would_stop"])
        self.assertTrue(decision["stop_is_hard_safety"])


if __name__ == "__main__":
    unittest.main()

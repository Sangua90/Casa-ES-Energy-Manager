"""Tests for deterministic managed-device dry-run admission."""

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "casa_es_energy_manager"
    / "device_dry_run.py"
)
SPEC = importlib.util.spec_from_file_location("casa_es_device_dry_run", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
evaluate_managed_devices = MODULE.evaluate_managed_devices


class DeviceDryRunTests(unittest.TestCase):
    def _data(self):
        return {
            "battery_soc": 80,
            "grid_headroom_w": 5750,
            "inverter_headroom_w": 8000,
            "phase_l1_headroom_w": 2500,
            "phase_l2_headroom_w": 2500,
            "phase_l3_headroom_w": 2500,
            "solar_after_house_w": 0,
            "pv_potential_after_house_w": 3000,
        }

    def _policy(self):
        return {
            "flexible_energy_budget_kwh": 8.0,
            "protect_grid_required": False,
            "target_reachability": "comfortable",
            "battery_first_preferred": False,
        }

    def _device(self, **overrides):
        device = {
            "subentry_id": "1",
            "name": "Boiler",
            "entity_id": "switch.boiler",
            "state": "off",
            "available": True,
            "nominal_power_w": 1500,
            "expected_runtime_minutes": 60,
            "priority": 5,
            "phase": "l1",
            "allow_grid": False,
            "enabled": True,
            "min_battery_soc": 40,
        }
        device.update(overrides)
        return device

    def test_device_is_admissible_with_solar_phase_and_energy_budget(self):
        result = evaluate_managed_devices(
            [self._device()], data=self._data(), policy=self._policy()
        )
        self.assertEqual(result["managed_devices_admissible_now"], 1)
        self.assertTrue(result["dry_run_decisions"][0]["would_start"])

    def test_future_energy_budget_does_not_start_load_at_night(self):
        data = self._data()
        data["pv_potential_after_house_w"] = 0
        result = evaluate_managed_devices(
            [self._device()], data=data, policy=self._policy()
        )
        self.assertEqual(result["managed_devices_admissible_now"], 0)
        self.assertEqual(result["dry_run_decisions"][0]["decision"], "waiting_solar")

    def test_numeric_priority_allocates_limited_current_solar(self):
        data = self._data()
        data["pv_potential_after_house_w"] = 2000
        low = self._device(
            subentry_id="low", name="Low", priority=8, nominal_power_w=1200
        )
        high = self._device(
            subentry_id="high", name="High", priority=2, nominal_power_w=1200
        )
        result = evaluate_managed_devices([low, high], data=data, policy=self._policy())
        decisions = {item["subentry_id"]: item for item in result["dry_run_decisions"]}
        self.assertTrue(decisions["high"]["would_start"])
        self.assertFalse(decisions["low"]["would_start"])
        self.assertEqual(decisions["low"]["decision"], "waiting_solar")
        self.assertEqual(decisions["high"]["priority"], 2)
        self.assertEqual(decisions["low"]["priority"], 8)

    def test_legacy_text_priority_remains_compatible(self):
        data = self._data()
        data["pv_potential_after_house_w"] = 2000
        old_low = self._device(
            subentry_id="old_low", name="Old low", priority="low", nominal_power_w=1200
        )
        old_high = self._device(
            subentry_id="old_high", name="Old high", priority="high", nominal_power_w=1200
        )
        result = evaluate_managed_devices(
            [old_low, old_high], data=data, policy=self._policy()
        )
        decisions = {item["subentry_id"]: item for item in result["dry_run_decisions"]}
        self.assertTrue(decisions["old_high"]["would_start"])
        self.assertFalse(decisions["old_low"]["would_start"])

    def test_phase_limit_blocks_start(self):
        data = self._data()
        data["phase_l1_headroom_w"] = 500
        result = evaluate_managed_devices(
            [self._device()], data=data, policy=self._policy()
        )
        self.assertEqual(result["dry_run_decisions"][0]["decision"], "blocked")
        self.assertIn("fase", result["dry_run_decisions"][0]["reason"].lower())

    def test_grid_allowed_load_can_start_without_solar(self):
        data = self._data()
        data["pv_potential_after_house_w"] = 0
        result = evaluate_managed_devices(
            [self._device(allow_grid=True)], data=data, policy=self._policy()
        )
        self.assertTrue(result["dry_run_decisions"][0]["would_start"])

    def test_running_load_reserves_future_energy_budget(self):
        running = self._device(
            subentry_id="running",
            state="on",
            nominal_power_w=2000,
            expected_runtime_minutes=120,
        )
        candidate = self._device(
            subentry_id="candidate",
            name="Candidate",
            nominal_power_w=1500,
            expected_runtime_minutes=120,
        )
        policy = self._policy()
        policy["flexible_energy_budget_kwh"] = 5.0
        result = evaluate_managed_devices(
            [running, candidate], data=self._data(), policy=policy
        )
        decisions = {item["subentry_id"]: item for item in result["dry_run_decisions"]}
        self.assertEqual(decisions["running"]["decision"], "already_running")
        self.assertEqual(decisions["candidate"]["decision"], "waiting_energy")

    def test_power_sensor_overrides_mode_state_for_idle_climate(self):
        climate = self._device(
            entity_id="climate.salone",
            state="cool",
            current_power_w=3,
        )
        result = evaluate_managed_devices(
            [climate], data=self._data(), policy=self._policy()
        )
        self.assertFalse(result["dry_run_decisions"][0]["running"])
        self.assertTrue(result["dry_run_decisions"][0]["would_start"])

    def test_power_sensor_marks_real_consumption_as_running(self):
        climate = self._device(
            entity_id="climate.salone",
            state="cool",
            current_power_w=700,
        )
        result = evaluate_managed_devices(
            [climate], data=self._data(), policy=self._policy()
        )
        self.assertTrue(result["dry_run_decisions"][0]["running"])
        self.assertEqual(result["dry_run_decisions"][0]["decision"], "already_running")

    def test_definite_shortfall_blocks_flexible_loads(self):
        policy = self._policy()
        policy["target_reachability"] = "definite_shortfall"
        result = evaluate_managed_devices(
            [self._device()], data=self._data(), policy=policy
        )
        self.assertFalse(result["dry_run_decisions"][0]["would_start"])
        self.assertIn("carenza", result["dry_run_decisions"][0]["reason"].lower())


if __name__ == "__main__":
    unittest.main()

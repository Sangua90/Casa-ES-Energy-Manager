"""Pure unit tests for Casa ES v1 preference and runtime-mode logic."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "casa_es_energy_manager"
PACKAGE = "casa_es_v1_test"

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


const = _load("const", "const.py")
base_dry = _load("device_dry_run", "device_dry_run.py")
v1_dry = _load("device_dry_run_v1", "device_dry_run_v1.py")
base_policy = _load("planner_policy", "planner_policy.py")
v1_policy = _load("planner_policy_v1", "planner_policy_v1.py")


class V1DeviceModeTests(unittest.TestCase):
    def data(self):
        return {
            "battery_soc": 80,
            "grid_headroom_w": 5000,
            "inverter_headroom_w": 7000,
            "phase_l1_headroom_w": 2500,
            "phase_l2_headroom_w": 2500,
            "phase_l3_headroom_w": 2500,
            "solar_after_house_w": 2000,
            "pv_potential_after_house_w": 2000,
        }

    def policy(self):
        return {
            "flexible_energy_budget_kwh": 8.0,
            "protect_grid_required": False,
            "target_reachability": "comfortable",
            "battery_first_preferred": False,
        }

    def device(self, **changes):
        value = {
            "subentry_id": "a",
            "name": "Clima",
            "entity_id": "climate.test",
            "state": "off",
            "available": True,
            "current_power_w": 0,
            "nominal_power_w": 1000,
            "admission_power_w": 1200,
            "expected_runtime_minutes": 60,
            "priority": 30,
            "phase": "l1",
            "allow_grid": False,
            "enabled": True,
            "min_battery_soc": 40,
            "seconds_since_change": 3600,
            "min_on_minutes": 20,
            "min_off_minutes": 20,
            "management_mode": "auto",
        }
        value.update(changes)
        return value

    def test_override_is_never_auto_started(self):
        result = v1_dry.evaluate_managed_devices(
            [self.device(management_mode="override")],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "manual_override")
        self.assertFalse(decision["would_start"])

    def test_off_mode_marks_running_load_for_future_stop(self):
        result = v1_dry.evaluate_managed_devices(
            [self.device(management_mode="off", state="cool", current_power_w=700)],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "forced_off")
        self.assertTrue(decision["would_stop"])

    def test_off_mode_does_not_reserve_future_energy(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    management_mode="off",
                    state="cool",
                    current_power_w=700,
                    expected_runtime_minutes=120,
                )
            ],
            data=self.data(),
            policy=self.policy(),
        )
        self.assertEqual(
            result["manual_override_running_energy_commitment_kwh"], 0
        )
        self.assertEqual(result["dry_run_running_energy_commitment_kwh"], 0)
        self.assertEqual(result["dry_run_remaining_flexible_budget_kwh"], 8.0)

    def test_override_running_load_can_reserve_known_cycle_energy(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    management_mode="override",
                    state="cool",
                    current_power_w=700,
                    expected_runtime_minutes=60,
                )
            ],
            data=self.data(),
            policy=self.policy(),
        )
        self.assertGreater(
            result["manual_override_running_energy_commitment_kwh"], 0
        )
        self.assertLess(result["dry_run_remaining_flexible_budget_kwh"], 8.0)

    def test_minimum_off_time_blocks_auto_restart(self):
        result = v1_dry.evaluate_managed_devices(
            [self.device(seconds_since_change=60, min_off_minutes=20)],
            data=self.data(),
            policy=self.policy(),
        )
        self.assertEqual(
            result["dry_run_decisions"][0]["decision"], "waiting_interval"
        )

    def test_missing_minimum_times_add_no_anti_cycle_block(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    seconds_since_change=1,
                    min_on_minutes=None,
                    min_off_minutes=None,
                )
            ],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["min_on_minutes"], 0)
        self.assertEqual(decision["min_off_minutes"], 0)
        self.assertNotEqual(decision["decision"], "waiting_interval")

    def test_optional_cycle_duration_does_not_invent_energy(self):
        result = v1_dry.evaluate_managed_devices(
            [self.device(expected_runtime_minutes=None)],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertIsNone(decision["expected_runtime_minutes"])
        self.assertIsNone(decision["expected_energy_kwh"])
        self.assertEqual(result["dry_run_running_energy_commitment_kwh"], 0)

    def test_priority_supports_full_one_to_one_hundred_range(self):
        devices = [
            self.device(subentry_id="low", name="B", priority=100),
            self.device(subentry_id="high", name="A", priority=1),
        ]
        result = base_dry.evaluate_managed_devices(
            devices, data=self.data(), policy=self.policy()
        )
        priorities = [item["priority"] for item in result["dry_run_decisions"]]
        ids = [item["subentry_id"] for item in result["dry_run_decisions"]]
        self.assertEqual(priorities, [1, 100])
        self.assertEqual(ids, ["high", "low"])

    def test_priority_is_clamped_at_one_hundred(self):
        result = base_dry.evaluate_managed_devices(
            [self.device(priority=999)], data=self.data(), policy=self.policy()
        )
        self.assertEqual(result["dry_run_decisions"][0]["priority"], 100)


class V1PreferenceTests(unittest.TestCase):
    def policy_for(self, preference: str):
        now = datetime(2026, 8, 26, 10, tzinfo=timezone.utc)
        target = now + timedelta(hours=2)
        curve = [
            {"time": now.isoformat(), "power_w": 4000},
            {"time": (now + timedelta(hours=1)).isoformat(), "power_w": 4000},
            {"time": target.isoformat(), "power_w": 4000},
        ]
        return v1_policy.build_planner_policy(
            {
                "battery_soc": 90,
                "grid_headroom_w": 5000,
                "inverter_headroom_w": 7000,
                "phase_l1_headroom_w": 2500,
                "phase_l2_headroom_w": 2500,
                "phase_l3_headroom_w": 2500,
                "pv_power_w": 4000,
                "pv_potential_w": 4000,
                "pv_potential_after_house_w": 2500,
                "forecast_curve": curve,
            },
            now=now,
            target=target,
            battery_capacity_kwh=14.3,
            battery_target_soc=100,
            expected_base_load_w=500,
            battery_charge_efficiency_pct=95,
            energy_preference=preference,
        )

    def test_battery_first_reserves_more_than_loads_first(self):
        battery = self.policy_for("battery_first")
        loads = self.policy_for("loads_first")
        self.assertLessEqual(
            battery["flexible_energy_budget_kwh"], loads["flexible_energy_budget_kwh"]
        )
        self.assertGreater(
            battery["flexible_energy_safety_buffer_kwh"],
            loads["flexible_energy_safety_buffer_kwh"],
        )


if __name__ == "__main__":
    unittest.main()

"""Pure unit tests for Casa ES preference, runtime modes and stop logic."""

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
    def data(self, **changes):
        value = {
            "battery_soc": 80,
            "grid_import_w": 0,
            "battery_discharge_w": 0,
            "grid_warning": False,
            "phase_warning": False,
            "inverter_warning": False,
            "grid_headroom_w": 5000,
            "inverter_headroom_w": 7000,
            "phase_l1_headroom_w": 2500,
            "phase_l2_headroom_w": 2500,
            "phase_l3_headroom_w": 2500,
            "solar_after_house_w": 2000,
            "pv_potential_after_house_w": 2000,
        }
        value.update(changes)
        return value

    def policy(self, **changes):
        value = {
            "flexible_energy_budget_kwh": 8.0,
            "protect_grid_required": False,
            "target_reachability": "comfortable",
            "battery_first_preferred": False,
        }
        value.update(changes)
        return value

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
            "max_grid_power_w": 100,
            "enabled": True,
            "min_battery_soc": 40,
            "seconds_since_change": 3600,
            "min_on_minutes": 20,
            "min_off_minutes": 20,
            "management_mode": "auto",
            "battery_discharge_override_w": 0,
            "on_only": False,
            "adaptive_shared_power_sensor": False,
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

    def test_override_is_never_auto_stopped_even_during_hard_safety(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    management_mode="override",
                    state="cool",
                    current_power_w=900,
                )
            ],
            data=self.data(phase_warning=True),
            policy=self.policy(protect_grid_required=True),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "manual_override")
        self.assertFalse(decision["would_stop"])

    def test_off_mode_marks_logically_active_load_for_future_stop(self):
        result = v1_dry.evaluate_managed_devices(
            [self.device(management_mode="off", state="cool", current_power_w=700)],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "forced_off")
        self.assertTrue(decision["would_stop"])

    def test_off_mode_stops_even_if_enabled_entity_is_currently_idle(self):
        result = v1_dry.evaluate_managed_devices(
            [self.device(management_mode="off", state="cool", current_power_w=6)],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertFalse(decision["running"])
        self.assertTrue(decision["entity_active"])
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

    def test_shared_meter_off_child_is_not_running_from_sibling_watts(self):
        """Regression for Riscaldamento SPA OFF while filter owns shared watts."""
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    entity_id="switch.spa_heat",
                    state="off",
                    current_power_w=30.3,
                    adaptive_shared_power_sensor=True,
                    nominal_power_w=2500,
                    admission_power_w=2500,
                )
            ],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertFalse(decision["running"])
        self.assertFalse(decision["entity_active"])

    def test_shared_meter_on_child_is_running_by_own_state(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    entity_id="switch.spa_filter",
                    state="on",
                    current_power_w=30.3,
                    adaptive_shared_power_sensor=True,
                    nominal_power_w=30,
                    admission_power_w=30,
                )
            ],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertTrue(decision["running"])
        self.assertTrue(decision["entity_active"])

    def test_enabled_idle_climate_is_not_repeatedly_started(self):
        result = v1_dry.evaluate_managed_devices(
            [self.device(state="cool", current_power_w=6)],
            data=self.data(),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "already_enabled_idle")
        self.assertFalse(decision["would_start"])
        self.assertTrue(decision["entity_active"])
        self.assertFalse(decision["running"])

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

    def test_battery_discharge_limit_requests_auto_stop_after_min_on(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    state="cool",
                    current_power_w=900,
                    battery_discharge_override_w=100,
                    seconds_since_change=3600,
                )
            ],
            data=self.data(battery_discharge_w=500),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "auto_stop")
        self.assertTrue(decision["would_stop"])
        self.assertFalse(decision["stop_is_hard_safety"])

    def test_minimum_on_delays_normal_energy_stop(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    state="cool",
                    current_power_w=900,
                    battery_discharge_override_w=100,
                    seconds_since_change=60,
                    min_on_minutes=20,
                )
            ],
            data=self.data(battery_discharge_w=500),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "minimum_on_protected")
        self.assertFalse(decision["would_stop"])

    def test_hard_electrical_safety_can_stop_before_minimum_on(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    state="cool",
                    current_power_w=900,
                    seconds_since_change=60,
                    min_on_minutes=20,
                )
            ],
            data=self.data(phase_warning=True),
            policy=self.policy(protect_grid_required=True),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "safety_stop")
        self.assertTrue(decision["would_stop"])
        self.assertTrue(decision["stop_is_hard_safety"])

    def test_on_only_cycle_resists_normal_energy_stop(self):
        result = v1_dry.evaluate_managed_devices(
            [
                self.device(
                    state="cool",
                    current_power_w=900,
                    battery_discharge_override_w=100,
                    seconds_since_change=3600,
                    on_only=True,
                )
            ],
            data=self.data(battery_discharge_w=500),
            policy=self.policy(),
        )
        decision = result["dry_run_decisions"][0]
        self.assertEqual(decision["decision"], "protected_cycle")
        self.assertFalse(decision["would_stop"])

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

"""Tests for Casa ES deterministic planner policy."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "casa_es_energy_manager"
    / "planner_policy.py"
)
SPEC = importlib.util.spec_from_file_location("casa_es_planner_policy", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
build_planner_policy = MODULE.build_planner_policy
apply_ai_guardrails = MODULE.apply_ai_guardrails
integrate_forecast_curve_kwh = MODULE.integrate_forecast_curve_kwh


class PlannerPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)

    def _base_data(self):
        return {
            "battery_soc": 80,
            "grid_headroom_w": 5750,
            "inverter_headroom_w": 9000,
            "phase_l1_headroom_w": 2500,
            "phase_l2_headroom_w": 2700,
            "phase_l3_headroom_w": 2600,
            "grid_warning": False,
            "phase_warning": False,
            "inverter_warning": False,
            "pv_power_w": 1000,
            "pv_potential_w": 1200,
            "pv_potential_after_house_w": 500,
            "pv_curtailment_likely": False,
            "forecast_remaining_kwh": 5.0,
            "forecast_curve": [],
        }

    def test_integrates_power_curve(self):
        target = self.now + timedelta(hours=2)
        curve = [
            {"time": self.now.isoformat(), "power_w": 1000},
            {"time": (self.now + timedelta(hours=1)).isoformat(), "power_w": 1000},
            {"time": target.isoformat(), "power_w": 1000},
        ]
        energy, complete = integrate_forecast_curve_kwh(
            curve, now=self.now, target=target
        )
        self.assertEqual(energy, 2.0)
        self.assertTrue(complete)

    def test_integrates_curve_across_midnight_to_next_day_target(self):
        now = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
        target = datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc)
        curve = [
            {"time": now.isoformat(), "power_w": 0},
            {"time": datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc).isoformat(), "power_w": 0},
            {"time": datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc).isoformat(), "power_w": 2000},
            {"time": datetime(2026, 8, 26, 11, 0, tzinfo=timezone.utc).isoformat(), "power_w": 4000},
            {"time": target.isoformat(), "power_w": 2000},
        ]
        energy, complete = integrate_forecast_curve_kwh(curve, now=now, target=target)
        self.assertIsNotNone(energy)
        self.assertGreater(energy, 10.0)
        self.assertTrue(complete)

    def test_cross_day_forecast_can_prove_target_has_solar_margin(self):
        now = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
        target = datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc)
        data = self._base_data()
        data["battery_soc"] = 79
        data["pv_power_w"] = 0
        data["pv_potential_w"] = 30
        data["forecast_remaining_kwh"] = 0
        data["forecast_curve"] = [
            {"time": now.isoformat(), "power_w": 0},
            {"time": datetime(2026, 8, 26, 5, 0, tzinfo=timezone.utc).isoformat(), "power_w": 0},
            {"time": datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc).isoformat(), "power_w": 3000},
            {"time": datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc).isoformat(), "power_w": 5000},
            {"time": target.isoformat(), "power_w": 2500},
        ]
        policy = build_planner_policy(
            data,
            now=now,
            target=target,
            battery_capacity_kwh=14.3,
            battery_target_soc=100,
        )
        self.assertTrue(policy["forecast_curve_complete_to_target"])
        self.assertGreater(policy["forecast_energy_to_target_kwh"], 3.003)
        self.assertFalse(policy["grid_charge_allowed"])
        self.assertFalse(policy["battery_first_preferred"])

    def test_protect_grid_not_allowed_with_large_headroom(self):
        data = self._base_data()
        policy = build_planner_policy(
            data,
            now=self.now,
            target=self.now + timedelta(hours=5),
            battery_capacity_kwh=14.3,
            battery_target_soc=100,
        )
        self.assertFalse(policy["protect_grid_allowed"])
        guarded = apply_ai_guardrails(
            {"strategy": "protect_grid", "allow_flexible_loads": False}, policy
        )
        self.assertNotEqual(guarded["strategy"], "protect_grid")
        self.assertTrue(guarded["guardrail_applied"])

    def test_critical_phase_forces_protect_grid(self):
        data = self._base_data()
        data["phase_l1_headroom_w"] = 100
        policy = build_planner_policy(
            data,
            now=self.now,
            target=self.now + timedelta(hours=5),
            battery_capacity_kwh=14.3,
            battery_target_soc=100,
        )
        self.assertTrue(policy["protect_grid_required"])
        guarded = apply_ai_guardrails(
            {
                "strategy": "balanced",
                "allow_flexible_loads": True,
                "grid_charge_recommended": True,
            },
            policy,
        )
        self.assertEqual(guarded["strategy"], "protect_grid")
        self.assertFalse(guarded["allow_flexible_loads"])

    def test_grid_charge_only_on_definite_shortfall(self):
        data = self._base_data()
        target = self.now + timedelta(hours=3)
        data["battery_soc"] = 60
        data["forecast_curve"] = [
            {"time": self.now.isoformat(), "power_w": 200},
            {"time": (self.now + timedelta(hours=1)).isoformat(), "power_w": 200},
            {"time": (self.now + timedelta(hours=2)).isoformat(), "power_w": 100},
            {"time": target.isoformat(), "power_w": 0},
        ]
        policy = build_planner_policy(
            data,
            now=self.now,
            target=target,
            battery_capacity_kwh=14.3,
            battery_target_soc=100,
        )
        self.assertTrue(policy["grid_charge_allowed"])
        self.assertEqual(policy["target_reachability"], "definite_shortfall")

    def test_solar_not_called_absent_when_potential_exists(self):
        data = self._base_data()
        data["pv_power_w"] = 20
        data["pv_potential_w"] = 178
        data["forecast_remaining_kwh"] = 0.0075
        policy = build_planner_policy(
            data,
            now=self.now,
            target=self.now + timedelta(hours=5),
            battery_capacity_kwh=14.3,
            battery_target_soc=100,
        )
        self.assertEqual(policy["solar_state"], "very_low")


if __name__ == "__main__":
    unittest.main()

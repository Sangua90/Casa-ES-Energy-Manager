"""Regression tests for daily target recovery and monitored switch semantics."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "casa_es_energy_manager"
PACKAGE = "casa_es_energy_manager_unit_test"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def _load_module(module_name: str, filename: str):
    full_name = f"{PACKAGE}.{module_name}"
    spec = importlib.util.spec_from_file_location(full_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


_load_module("const", "const.py")
daily_target = _load_module("daily_target", "daily_target.py")
control_semantics = _load_module(
    "monitored_control_semantics", "monitored_control_semantics.py"
)


class DailyTargetTests(unittest.TestCase):
    def test_before_deadline_plans_to_configured_hour(self) -> None:
        now = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)
        result = daily_target.daily_battery_target_window(now, 15)
        self.assertEqual(result["mode"], "deadline")
        self.assertTrue(result["target_active"])
        self.assertEqual(
            result["planning_target"],
            datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc),
        )

    def test_after_deadline_recovers_while_solar_opportunity_exists(self) -> None:
        now = datetime(2026, 8, 26, 17, 0, tzinfo=timezone.utc)
        result = daily_target.daily_battery_target_window(
            now,
            15,
            recovery_solar_available=True,
        )
        self.assertEqual(result["mode"], "recovery_with_solar")
        self.assertTrue(result["target_active"])
        self.assertEqual(
            result["planning_target"],
            datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc),
        )

    def test_after_deadline_closes_target_when_solar_is_finished(self) -> None:
        now = datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc)
        result = daily_target.daily_battery_target_window(
            now,
            15,
            recovery_solar_available=False,
        )
        self.assertEqual(result["mode"], "day_complete_no_solar")
        self.assertFalse(result["target_active"])
        self.assertEqual(result["planning_target"], now)

    def test_temporary_cloud_does_not_close_recovery_if_forecast_remains(self) -> None:
        self.assertTrue(
            daily_target.solar_recovery_available(
                {
                    "pv_power_w": 0,
                    "pv_potential_w": 50,
                    "forecast_remaining_kwh": 0.3,
                }
            )
        )

    def test_recovery_closes_when_real_potential_and_forecast_are_empty(self) -> None:
        self.assertFalse(
            daily_target.solar_recovery_available(
                {
                    "pv_power_w": 1,
                    "pv_potential_w": 1,
                    "forecast_remaining_kwh": 0,
                }
            )
        )

    def test_midnight_starts_new_daily_cycle(self) -> None:
        now = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)
        result = daily_target.daily_battery_target_window(
            now,
            15,
            recovery_solar_available=False,
        )
        self.assertEqual(result["mode"], "deadline")
        self.assertTrue(result["target_active"])
        self.assertEqual(
            result["planning_target"],
            datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc),
        )


class SwitchResumeTests(unittest.TestCase):
    def test_switch_uses_same_entity_for_resume_when_second_field_is_empty(self) -> None:
        entity, source = control_semantics.effective_resume_entity(
            "switch.stufetta", None
        )
        self.assertEqual(entity, "switch.stufetta")
        self.assertEqual(source, "same_switch")

    def test_explicit_resume_always_wins(self) -> None:
        entity, source = control_semantics.effective_resume_entity(
            "switch.stufetta", "button.resume"
        )
        self.assertEqual(entity, "button.resume")
        self.assertEqual(source, "explicit")

    def test_pause_button_without_resume_stays_manual(self) -> None:
        entity, source = control_semantics.effective_resume_entity(
            "button.lavastoviglie_pausa", None
        )
        self.assertEqual(entity, "")
        self.assertEqual(source, "manual")

    def test_runtime_wires_v143_and_keeps_v142_resume_compatibility(self) -> None:
        init_source = (ROOT / "__init__.py").read_text(encoding="utf-8")
        coordinator142 = (ROOT / "coordinator_v142.py").read_text(encoding="utf-8")
        coordinator143 = (ROOT / "coordinator_v143.py").read_text(encoding="utf-8")
        ai_source = (ROOT / "ai_planner_v1.py").read_text(encoding="utf-8")
        self.assertIn("coordinator_v143", init_source)
        self.assertIn("effective_resume_entity", coordinator142)
        self.assertIn("solar_recovery_available", coordinator143)
        self.assertIn('policy["grid_charge_allowed"] = False', coordinator143)
        self.assertIn("battery_target_mode", ai_source)
        self.assertIn("battery_target_deadline", ai_source)


if __name__ == "__main__":
    unittest.main()

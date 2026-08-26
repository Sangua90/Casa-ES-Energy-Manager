"""Regression tests for v1.4.2 daily target and monitored switch semantics."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "casa_es_energy_manager"


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


daily_target = _load_module("casa_es_daily_target_test", "daily_target.py")
control_semantics = _load_module(
    "casa_es_monitored_control_semantics_test", "monitored_control_semantics.py"
)


class DailyTargetTests(unittest.TestCase):
    def test_before_deadline_plans_to_configured_hour(self) -> None:
        now = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)
        result = daily_target.daily_battery_target_window(now, 15)
        self.assertEqual(result["mode"], "deadline")
        self.assertEqual(
            result["planning_target"],
            datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc),
        )

    def test_at_and_after_deadline_recovers_until_midnight(self) -> None:
        for hour in (15, 18, 23):
            now = datetime(2026, 8, 26, hour, 0, tzinfo=timezone.utc)
            result = daily_target.daily_battery_target_window(now, 15)
            self.assertEqual(result["mode"], "recovery_until_midnight")
            self.assertEqual(
                result["deadline"],
                datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(
                result["planning_target"],
                datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc),
            )

    def test_midnight_starts_new_daily_cycle(self) -> None:
        now = datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)
        result = daily_target.daily_battery_target_window(now, 15)
        self.assertEqual(result["mode"], "deadline")
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

    def test_runtime_wires_v142_coordinator_and_ai_context(self) -> None:
        init_source = (ROOT / "__init__.py").read_text(encoding="utf-8")
        coordinator_source = (ROOT / "coordinator_v142.py").read_text(encoding="utf-8")
        ai_source = (ROOT / "ai_planner_v1.py").read_text(encoding="utf-8")
        self.assertIn("coordinator_v142", init_source)
        self.assertIn("daily_battery_target_window", coordinator_source)
        self.assertIn("effective_resume_entity", coordinator_source)
        self.assertIn("battery_target_mode", ai_source)
        self.assertIn("battery_target_deadline", ai_source)


if __name__ == "__main__":
    unittest.main()

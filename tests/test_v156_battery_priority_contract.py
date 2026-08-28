"""Regression contracts for Casa ES Energy Manager v1.5.6 retained by later releases."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V156BatteryPriorityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (COMPONENT / "coordinator_v156.py").read_text(encoding="utf-8")
        self.source157 = (COMPONENT / "coordinator_v157.py").read_text(encoding="utf-8")
        self.init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    def test_battery_charge_cap_is_explicit(self) -> None:
        self.assertIn("BATTERY_MAX_CHARGE_W = 3500.0", self.source)
        self.assertIn("battery_input_energy_needed_kwh", self.source)
        self.assertIn("required_w", self.source)
        self.assertIn("overflow_w", self.source)

    def test_true_overflow_can_feed_flexible_loads_by_priority(self) -> None:
        self.assertIn("battery_cap_overflow_start", self.source)
        self.assertIn('key=lambda item: int(item.get("priority") or 50)', self.source)
        self.assertIn("remaining_overflow", self.source)

    def test_bad_battery_trajectory_forces_low_priority_recovery_stops(self) -> None:
        self.assertIn("battery_trajectory_recovery_stop", self.source)
        self.assertIn("hard_recovery", self.source)
        self.assertIn("can_auto_stop", self.source)

    def test_thermal_priority_competes_with_climate_priority(self) -> None:
        self.assertIn("_thermal_action_priority", self.source)
        self.assertIn("waiting_higher_priority_thermal", self.source)
        self.assertIn("thermal_priority", self.source)

    def test_phase_warning_is_masked_in_all_real_control_paths(self) -> None:
        self.assertIn('data["phase_warning"] = False', self.source)
        self.assertIn('data["phase_warning"] = raw_phase_warning', self.source)
        self.assertIn('"disabled_all_control_paths"', self.source)

    def test_legacy_twenty_twenty_is_persisted_as_twenty_five(self) -> None:
        self.assertIn("_persist_climate_anti_cycle_migration", self.init)
        self.assertIn("async_update_subentry", self.init)
        self.assertIn("CONF_DEVICE_MIN_OFF_MINUTES] = 5.0", self.init)

    def test_v156_remains_in_v157_release_chain(self) -> None:
        manifest = (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        const = (COMPONENT / "const.py").read_text(encoding="utf-8")
        self.assertIn("V156Coordinator", self.source157)
        self.assertIn("coordinator_v157", self.init)
        self.assertIn('"version": "1.5.7"', manifest)
        self.assertIn('VERSION = "1.5.7"', const)


if __name__ == "__main__":
    unittest.main()

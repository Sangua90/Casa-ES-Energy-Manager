"""Regression contracts for Casa ES Energy Manager v1.5.3."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V153GridAndSolarTargetContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (COMPONENT / "coordinator_v153.py").read_text(encoding="utf-8")

    def test_phase_only_warning_is_advisory_for_real_control(self) -> None:
        self.assertIn('"balancing_advisory_only"', self.source)
        self.assertIn('data["phase_warning"] = False', self.source)
        self.assertIn('not data.get("inverter_warning")', self.source)
        self.assertIn('return bool(data.get("grid_warning") or data.get("inverter_warning"))', self.source)

    def test_phase_headrooms_are_still_kept_for_balancing(self) -> None:
        self.assertIn("phase_l1_headroom_w", self.source)
        self.assertIn("phase_l2_headroom_w", self.source)
        self.assertIn("phase_l3_headroom_w", self.source)
        self.assertIn("Start admission still uses the original phase", self.source)

    def test_intervention_snapshot_contains_exact_electrical_context(self) -> None:
        for key in (
            "phase_l1_power_w",
            "phase_l2_power_w",
            "phase_l3_power_w",
            "grid_import_w",
            "load_power_w",
            "pv_power_w",
            "battery_soc",
            "active_loads_over_20w",
            "last_intervention_snapshot",
        ):
            self.assertIn(key, self.source)

    def test_battery_target_uses_end_of_useful_solar(self) -> None:
        self.assertIn("DYNAMIC_SOLAR_USEFUL_MIN_W = 500.0", self.source)
        self.assertIn("DYNAMIC_SOLAR_TARGET_BUFFER_MINUTES = 30", self.source)
        self.assertIn("solar_end - timedelta", self.source)
        self.assertIn("battery_target_dynamic_from_solar_end", self.source)
        self.assertIn("Forecast curve unavailable", self.source)

    def test_v153_remains_in_v156_release_chain(self) -> None:
        init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        source154 = (COMPONENT / "coordinator_v154.py").read_text(encoding="utf-8")
        source155 = (COMPONENT / "coordinator_v155.py").read_text(encoding="utf-8")
        source156 = (COMPONENT / "coordinator_v156.py").read_text(encoding="utf-8")
        manifest = (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        const = (COMPONENT / "const.py").read_text(encoding="utf-8")
        self.assertIn("V153Coordinator", source154)
        self.assertIn("V154Coordinator", source155)
        self.assertIn("V155Coordinator", source156)
        self.assertIn("coordinator_v156", init_source)
        self.assertIn('"version": "1.5.6"', manifest)
        self.assertIn('VERSION = "1.5.6"', const)


if __name__ == "__main__":
    unittest.main()

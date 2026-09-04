"""Regression contracts for Casa ES Energy Manager v1.5.17 SOC hysteresis."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V1517BatterySocHysteresisContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (COMPONENT / "coordinator_v1517.py").read_text(encoding="utf-8")
        self.init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.manifest = (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        self.const = (COMPONENT / "const.py").read_text(encoding="utf-8")

    def test_default_band_is_plus_minus_five_percentage_points(self) -> None:
        self.assertIn("BATTERY_SOC_HYSTERESIS_PCT = 5.0", self.source)
        self.assertIn("configured - BATTERY_SOC_HYSTERESIS_PCT", self.source)
        self.assertIn("configured + BATTERY_SOC_HYSTERESIS_PCT", self.source)

    def test_release_requires_upper_edge_and_recovery_uses_lower_edge(self) -> None:
        self.assertIn("soc >= upper", self.source)
        self.assertIn("soc <= lower", self.source)
        self.assertIn("lower if released else upper", self.source)

    def test_state_is_held_inside_band(self) -> None:
        self.assertIn("_battery_soc_load_released", self.source)
        self.assertIn("restart_inside_band", self.source)
        self.assertIn("battery_priority_until_upper_boundary", self.source)

    def test_existing_min_soc_machinery_receives_effective_threshold(self) -> None:
        self.assertIn("configured_min_battery_soc", self.source)
        self.assertIn("effective_min_battery_soc", self.source)
        self.assertIn("CONF_DEVICE_MIN_BATTERY_SOC", self.source)

    def test_release_chain_and_version_are_v1517(self) -> None:
        self.assertIn("coordinator_v1517", self.init)
        self.assertIn('"version": "1.5.17"', self.manifest)
        self.assertIn('VERSION = "1.5.17"', self.const)


if __name__ == "__main__":
    unittest.main()

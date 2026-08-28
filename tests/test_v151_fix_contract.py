"""Regression contracts for Casa ES Energy Manager v1.5.1 fixes retained by later releases."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V151FixContractTests(unittest.TestCase):
    def test_climate_anti_cycle_uses_real_observed_transitions(self) -> None:
        source = (COMPONENT / "coordinator_v151.py").read_text(encoding="utf-8")
        self.assertIn("_last_real_transition_at", source)
        self.assertIn("First observation after integration start/reload", source)
        self.assertIn('anti_cycle_transition_source"] = "real_observed_transition"', source)

    def test_legacy_twenty_twenty_climate_profile_becomes_twenty_five(self) -> None:
        source = (COMPONENT / "coordinator_v151.py").read_text(encoding="utf-8")
        self.assertIn("abs(min_on - 20.0)", source)
        self.assertIn("abs(min_off - 20.0)", source)
        self.assertIn("= 5.0", source)

    def test_independent_safety_margins(self) -> None:
        source = (COMPONENT / "calculations.py").read_text(encoding="utf-8")
        self.assertIn("phase_margin = 150.0", source)
        self.assertIn("grid_margin = 300.0", source)
        self.assertIn("inverter_margin", source)

    def test_v151_remains_in_release_chain(self) -> None:
        source152 = (COMPONENT / "coordinator_v152.py").read_text(encoding="utf-8")
        source153 = (COMPONENT / "coordinator_v153.py").read_text(encoding="utf-8")
        source154 = (COMPONENT / "coordinator_v154.py").read_text(encoding="utf-8")
        source155 = (COMPONENT / "coordinator_v155.py").read_text(encoding="utf-8")
        source156 = (COMPONENT / "coordinator_v156.py").read_text(encoding="utf-8")
        source157 = (COMPONENT / "coordinator_v157.py").read_text(encoding="utf-8")
        init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        manifest = (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        const = (COMPONENT / "const.py").read_text(encoding="utf-8")
        self.assertIn("coordinator_v151", source152)
        self.assertIn("coordinator_v152", source153)
        self.assertIn("V153Coordinator", source154)
        self.assertIn("V154Coordinator", source155)
        self.assertIn("V155Coordinator", source156)
        self.assertIn("V156Coordinator", source157)
        self.assertIn("coordinator_v157", init_source)
        self.assertIn('"version": "1.5.7"', manifest)
        self.assertIn('VERSION = "1.5.7"', const)


if __name__ == "__main__":
    unittest.main()

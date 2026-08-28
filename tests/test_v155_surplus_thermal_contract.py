"""Regression contracts for Casa ES Energy Manager v1.5.5 retained by later releases."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V155SurplusThermalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (COMPONENT / "coordinator_v155.py").read_text(encoding="utf-8")
        self.flow = (COMPONENT / "managed_device_flow_v15.py").read_text(encoding="utf-8")

    def test_virtual_pv_is_guarded_solar_opportunity(self) -> None:
        self.assertIn("pv_potential_after_house_w", self.source)
        self.assertIn("VIRTUAL_SURPLUS_GRID_IMPORT_MAX_W = 100.0", self.source)
        self.assertIn("not data.get(\"grid_warning\")", self.source)
        self.assertIn("not data.get(\"inverter_warning\")", self.source)
        self.assertIn("target_reachability", self.source)
        self.assertIn("_curtailment_harvest_available", self.source)

    def test_thermal_target_ramps_to_normal_max_near_solar_end(self) -> None:
        self.assertIn("THERMAL_FULL_TARGET_MINUTES = 60.0", self.source)
        self.assertIn("solar_useful_end", self.source)
        self.assertIn("normal_max", self.source)
        self.assertIn("ultimo FV utile", self.source)
        self.assertIn("accumulo termico FV", self.source)

    def test_generic_and_thermal_control_share_one_curtailment_signal(self) -> None:
        self.assertIn('data["curtailment_likely"] = unified', self.source)
        self.assertIn("pv_curtailment_likely", self.source)
        self.assertIn("v155_unified_curtailment_signal", self.source)

    def test_visible_climate_profile_is_twenty_five(self) -> None:
        self.assertIn("CLIMATE_DEFAULT_MIN_ON_MINUTES = 20.0", self.flow)
        self.assertIn("CLIMATE_DEFAULT_MIN_OFF_MINUTES = 5.0", self.flow)
        self.assertIn("_apply_climate_cycle_profile", self.flow)
        self.assertIn("abs(min_on - 20.0)", self.flow)
        self.assertIn("abs(min_off - 20.0)", self.flow)

    def test_v155_remains_in_v157_release_chain(self) -> None:
        init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        source156 = (COMPONENT / "coordinator_v156.py").read_text(encoding="utf-8")
        source157 = (COMPONENT / "coordinator_v157.py").read_text(encoding="utf-8")
        manifest = (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        const = (COMPONENT / "const.py").read_text(encoding="utf-8")
        self.assertIn("V155Coordinator", source156)
        self.assertIn("V156Coordinator", source157)
        self.assertIn("coordinator_v157", init_source)
        self.assertIn('"version": "1.5.7"', manifest)
        self.assertIn('VERSION = "1.5.7"', const)


if __name__ == "__main__":
    unittest.main()

"""Static regression contracts for Casa ES Energy Manager v1.5."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V15LearningAndThermalContractTests(unittest.TestCase):
    def test_manual_mode_is_excluded_from_adaptive_learning(self) -> None:
        source = (COMPONENT / "adaptive_learning_v15.py").read_text(encoding="utf-8")
        self.assertIn("management_mode", source)
        self.assertIn("DEVICE_MODE_AUTO", source)
        self.assertIn("learning_excluded", source)

    def test_thermal_profile_learns_both_sources_and_losses(self) -> None:
        source = (COMPONENT / "thermal_learning_v15.py").read_text(encoding="utf-8")
        self.assertIn("heat_pump_c_per_h", source)
        self.assertIn("resistance_c_per_h", source)
        self.assertIn("standby_loss_c_per_h", source)
        self.assertIn("draw_by_hour", source)
        self.assertIn("heat_pump_kwh_per_c", source)
        self.assertIn("resistance_kwh_per_c", source)

    def test_legionella_is_absolute_thermal_override(self) -> None:
        source = (COMPONENT / "coordinator_v15.py").read_text(encoding="utf-8")
        self.assertIn("thermal_legionella_active", source)
        self.assertIn('exclusion = "legionella"', source)
        self.assertIn("Internal Ariston legionella cycle owns the appliance completely", source)

    def test_manual_boost_is_not_taken_over(self) -> None:
        source = (COMPONENT / "coordinator_v15.py").read_text(encoding="utf-8")
        self.assertIn('exclusion = "manual_or_appliance_boost"', source)
        self.assertIn("User/app initiated Boost", source)

    def test_thermal_storage_never_uses_generic_water_heater_on_off(self) -> None:
        source = (COMPONENT / "coordinator_v15.py").read_text(encoding="utf-8")
        self.assertIn("Keep thermal storage out of generic on/off control", source)
        self.assertIn("thermal_ids", source)
        self.assertIn("_set_water_temperature", source)
        self.assertIn("_set_boost", source)


if __name__ == "__main__":
    unittest.main()

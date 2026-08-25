"""Tests for Casa ES Energy Manager calculations."""

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "casa_es_energy_manager"
    / "calculations.py"
)
SPEC = importlib.util.spec_from_file_location("casa_es_calculations", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
calculate_metrics = MODULE.calculate_metrics


class CalculationTests(unittest.TestCase):
    def test_phase_warning_and_headroom(self):
        data = calculate_metrics(
            pv_power_w=6000,
            load_power_w=4200,
            grid_power_w=0,
            battery_power_w=1800,
            phase_l1_w=2900,
            phase_l2_w=700,
            phase_l3_w=600,
            inverter_limit_w=10000,
            phase_limit_w=3000,
            grid_limit_w=6000,
            safety_margin_w=250,
        )
        self.assertTrue(data["phase_warning"])
        self.assertEqual(data["phase_l1_headroom_w"], 0)
        self.assertEqual(data["phase_l2_headroom_w"], 2050)
        self.assertEqual(data["status"], "phase_warning")

    def test_grid_warning_has_priority(self):
        data = calculate_metrics(
            pv_power_w=1000,
            load_power_w=7000,
            grid_power_w=5900,
            battery_power_w=-100,
            phase_l1_w=1000,
            phase_l2_w=1000,
            phase_l3_w=1000,
            inverter_limit_w=10000,
            phase_limit_w=3000,
            grid_limit_w=6000,
            safety_margin_w=250,
        )
        self.assertTrue(data["grid_warning"])
        self.assertEqual(data["status"], "grid_warning")

    def test_sign_conventions(self):
        data = calculate_metrics(
            pv_power_w=5000,
            load_power_w=2500,
            grid_power_w=-300,
            battery_power_w=-700,
            phase_l1_w=None,
            phase_l2_w=None,
            phase_l3_w=None,
            inverter_limit_w=10000,
            phase_limit_w=3000,
            grid_limit_w=6000,
            safety_margin_w=250,
        )
        self.assertEqual(data["grid_import_w"], 0)
        self.assertEqual(data["grid_export_w"], 300)
        self.assertEqual(data["battery_charge_w"], 0)
        self.assertEqual(data["battery_discharge_w"], 700)
        self.assertEqual(data["solar_after_house_w"], 2500)

    def test_potential_pv_never_below_measured(self):
        data = calculate_metrics(
            pv_power_w=5000,
            pv_potential_power_w=4000,
            load_power_w=1500,
            grid_power_w=0,
            battery_power_w=1000,
            battery_soc=80,
            phase_l1_w=None,
            phase_l2_w=None,
            phase_l3_w=None,
            inverter_limit_w=10000,
            phase_limit_w=3000,
            grid_limit_w=6000,
            safety_margin_w=250,
        )
        self.assertEqual(data["pv_potential_w"], 5000)
        self.assertEqual(data["pv_potential_gap_w"], 0)
        self.assertEqual(data["pv_potential_after_house_w"], 3500)
        self.assertFalse(data["pv_curtailment_likely"])

    def test_likely_zero_export_curtailment(self):
        data = calculate_metrics(
            pv_power_w=800,
            pv_potential_power_w=5000,
            load_power_w=700,
            grid_power_w=0,
            battery_power_w=0,
            battery_soc=100,
            phase_l1_w=300,
            phase_l2_w=200,
            phase_l3_w=200,
            inverter_limit_w=10000,
            phase_limit_w=3000,
            grid_limit_w=6000,
            safety_margin_w=250,
        )
        self.assertEqual(data["pv_potential_w"], 5000)
        self.assertEqual(data["pv_potential_gap_w"], 4200)
        self.assertEqual(data["pv_potential_after_house_w"], 4300)
        self.assertTrue(data["pv_curtailment_likely"])
        self.assertEqual(data["status"], "pv_curtailment_likely")


if __name__ == "__main__":
    unittest.main()

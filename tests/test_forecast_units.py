"""Tests for forecast input unit handling."""

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "casa_es_energy_manager"
    / "forecast_units.py"
)
SPEC = importlib.util.spec_from_file_location("casa_es_forecast_units", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
normalize_forecast_measure = MODULE.normalize_forecast_measure


class ForecastUnitTests(unittest.TestCase):
    def test_watts_stay_power(self):
        power_w, energy_kwh = normalize_forecast_measure(635, "W")
        self.assertEqual(power_w, 635)
        self.assertIsNone(energy_kwh)

    def test_kw_normalizes_to_watts(self):
        power_w, energy_kwh = normalize_forecast_measure(1.5, "kW")
        self.assertEqual(power_w, 1500)
        self.assertIsNone(energy_kwh)

    def test_kwh_stays_energy(self):
        power_w, energy_kwh = normalize_forecast_measure(0.39175, "kWh")
        self.assertIsNone(power_w)
        self.assertEqual(energy_kwh, 0.39175)

    def test_wh_normalizes_to_kwh(self):
        power_w, energy_kwh = normalize_forecast_measure(750, "Wh")
        self.assertIsNone(power_w)
        self.assertEqual(energy_kwh, 0.75)


if __name__ == "__main__":
    unittest.main()

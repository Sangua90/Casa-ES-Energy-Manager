"""Numerical protocol, contamination filtering and bounded persistence."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "energy_meter_engine"))
from engine import Engine


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = Engine(Path(self.directory.name) / "models.json")
        self.now = datetime(2026, 10, 7, 18, tzinfo=timezone(timedelta(hours=2)))

    def payload(self):
        return {"schema": 1, "timestamp": self.now.isoformat(), "house": {"reviewed": True},
                "zones": [], "energy": {"pv_remaining_kwh": 8, "battery_capacity_kwh": 20,
                                          "soc": 50, "target_soc": 80, "house_remaining_kwh": 1}}

    def test_battery_capacity_is_configured_and_budget_not_double_counted(self):
        payload = self.payload()
        result = self.engine.plan(payload)
        self.assertEqual(result["energy_budget"]["battery_need_kwh"], 6)
        self.assertEqual(result["energy_budget"]["unallocated_kwh"], 1)
        payload["energy"]["battery_capacity_kwh"] = 40
        self.assertEqual(self.engine.plan(payload)["energy_budget"]["unallocated_kwh"], 0)

    def test_invalid_schema_and_naive_timestamp_rejected(self):
        for changes in ({"schema": 2}, {"timestamp": "2026-10-07T18:00:00"}):
            with self.assertRaises(ValueError):
                self.engine.plan({**self.payload(), **changes})

    def test_missing_forecast_is_degraded_without_disabling_room_planning(self):
        payload = self.payload()
        payload["energy"].pop("pv_remaining_kwh")
        result = self.engine.plan(payload)
        self.assertEqual(result["state"], "DEGRADED")
        self.assertIn("forecast_unavailable", result["reasons"])

    def test_contaminated_or_changed_source_does_not_train(self):
        zone = {"id": "one", "temperature": 18, "thermal_source": "heat_pump", "contamination": ["manual"]}
        self.engine.learn(zone, 1000)
        self.engine.learn({**zone, "temperature": 19}, 4600)
        self.assertEqual(self.engine.models, {})
        self.engine.learn({**zone, "contamination": []}, 5000)
        self.engine.learn({**zone, "temperature": 20, "thermal_source": "off", "contamination": []}, 8600)
        self.assertEqual(self.engine.models, {})

    def test_short_samples_accumulate_before_learning_and_survive_restart(self):
        zone = {"id": "one", "temperature": 18, "thermal_source": "heat_pump", "contamination": []}
        self.engine.learn(zone, 1000)
        for seconds in range(60, 901, 60):
            self.engine.learn({**zone, "temperature": 18 + seconds / 3600}, 1000 + seconds)
        self.assertEqual(self.engine.models["one"]["heat_pump"]["samples"], 1)
        self.assertAlmostEqual(self.engine.models["one"]["heat_pump"]["rate_c_h"], 1)
        self.engine.save(2000)
        restored = Engine(self.engine.path)
        self.assertEqual(restored.models, self.engine.models)

    def test_packaged_room_planner_matches_integration(self):
        self.assertEqual((ROOT / "energy_meter_engine/house_climate_plan.py").read_text(),
                         (ROOT / "custom_components/casa_es_energy_manager/house_climate_plan.py").read_text())


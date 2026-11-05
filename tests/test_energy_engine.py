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

    def test_verified_battery_input_need_includes_charging_losses(self):
        payload = self.payload()
        payload["energy"]["battery_input_need_kwh"] = 6.5
        result = self.engine.plan(payload)
        self.assertEqual(result["energy_budget"]["battery_need_kwh"], 6.5)
        self.assertEqual(result["energy_budget"]["unallocated_kwh"], .5)

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

    def preparation(self, hour=14, hp_only=False, electrical=True):
        room = {"reviewed": True, "heat_pump_entity": "climate.hp", "radiator_entities": ["climate.trv"],
                "heat_pump_only": hp_only, "comfort_temperature": 20, "heat_pump_cop": 4,
                "cop_confirmed": True, "weekday_enabled": True,
                "weekday_start": "18:00:00", "weekday_end": "19:00:00"}
        self.engine.models["r"] = {"gas": {"rate_c_h": .2, "samples": 20},
                                   "heat_pump": {"rate_c_h": 2, "samples": 20},
                                   "off": {"rate_c_h": -.4, "samples": 20}}
        payload = self.payload()
        payload.update(timestamp=self.now.replace(hour=hour).isoformat(),
                       house={"reviewed": True, "season": "winter", "hydraulics_confirmed": True,
                              "economics_confirmed": True, "allow_economic_grid": True},
                       zones=[{"id": "r", "config": room, "temperature": 18,
                               "electrical_ok": electrical}])
        return self.engine.plan(payload)["zones"]["r"]

    def test_economic_preparation_uses_hp_rate_not_slow_radiator(self):
        d = self.preparation()
        self.assertEqual(d["preheat_source"], "heat_pump")
        # Two degrees now + four hours of .4 C/h cooling; heating replaces
        # passive cooling during the preparation interval.
        self.assertAlmostEqual(d["preheat_minutes"], 3.6 / 2.4 * 60 * 1.25 + 15)

    def test_active_period_does_not_project_loss_until_tomorrow(self):
        d = self.preparation(hour=18)
        self.assertEqual(d["preheat_minutes"], 0)
        self.assertEqual(d["preheat_basis"], "active_period")
        self.assertIsNone(d["deadline"])
        self.assertTrue(d["occupied"])

    def test_real_electrical_restriction_uses_gas_preparation(self):
        d = self.preparation(electrical=False)
        self.assertEqual(d["preheat_source"], "gas")
        self.assertGreater(d["preheat_minutes"], 300)

    def test_hp_only_never_trains_preparation_from_radiator_rate(self):
        d = self.preparation(hp_only=True, electrical=False)
        self.assertEqual(d["preheat_source"], "heat_pump")

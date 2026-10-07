"""Recorder replay, model identifiability, shared budget and expiring overrides."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "energy_meter_engine"))
from engine import Engine
from thermal_model import estimate, fit_context
from horizon import optimize
from house_climate_plan import target_level, situation, next_deadline, celsius, auxiliary_active

package = types.ModuleType("climate_test_package")
package.__path__ = [str(ROOT / "custom_components/casa_es_energy_manager")]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location("climate_test_package.climate_history", ROOT / "custom_components/casa_es_energy_manager/climate_history.py")
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)
NOW = datetime(2026, 10, 7, 16, tzinfo=timezone(timedelta(hours=2)))


class PredictiveTests(unittest.TestCase):
    def room(self):
        return {"name": "Camera", "reviewed": True, "machine": "p1", "heat_pump_entity": "climate.hp",
                "temperature_entity": "sensor.room", "comfort_temperature": 21, "maintenance_temperature": 17,
                "weekday_enabled": True, "weekday_start": "17:00:00", "weekday_end": "20:00:00", "preheat_minutes": 90,
                "power_entity": "sensor.power", "nominal_power_w": 1400, "window_entities": ["binary_sensor.window"]}

    def test_replay_keeps_attributes_and_absolute_timestamps(self):
        def row(state, attributes):
            return {"last_updated": NOW.isoformat(), "state": state, "attributes": attributes}
        rows = {"sensor.room": [row("68", {"unit_of_measurement": "°F"})],
                "climate.hp": [row("heat", {"current_temperature": 21, "realtime_power": 800})],
                "sensor.power": [row(".8", {"unit_of_measurement": "kW"})],
                "binary_sensor.window": [row("off", {})]}
        points = history.replay(rows, {"r": self.room()}, {}, NOW, NOW + timedelta(hours=1))
        self.assertEqual(len(points), 5)
        self.assertEqual(points[0]["zones"][0]["temperature"], 20)
        self.assertEqual(points[0]["zones"][0]["power_w"], 800)
        self.assertEqual(points[1]["at"] - points[0]["at"], 900)

    def test_missing_open_window_and_unknown_source_contaminate(self):
        points = history.replay({}, {"r": self.room()}, {}, NOW, NOW + timedelta(hours=1))
        self.assertIn("window_open_or_unknown", points[0]["zones"][0]["contamination"])
        self.assertIn("heat_pump_state_missing", points[0]["zones"][0]["contamination"])

    def test_heat_mode_with_standby_power_is_not_heating(self):
        def row(state, attributes):
            return {"last_updated": NOW.isoformat(), "state": state, "attributes": attributes}
        rows = {"climate.hp": [row("heat", {"current_temperature": 21})],
                "sensor.power": [row("14", {"unit_of_measurement": "W"})],
                "binary_sensor.window": [row("off", {})]}
        point = history.replay(rows, {"r": self.room()}, {}, NOW, NOW + timedelta(hours=1))[0]
        self.assertEqual(point["zones"][0]["thermal_source"], "off")

    def test_weekday_early_routine_does_not_apply_on_other_days(self):
        room = {**self.room(), "weekday_start": "18:00:00", "weekday_early_start": "16:30:00", "weekday_early_days": ["0", "2", "4"]}
        self.assertEqual(next_deadline(room, NOW).hour, 16)
        self.assertEqual(next_deadline(room, NOW - timedelta(days=1)).hour, 18)

    def test_outdoor_freezing_fahrenheit_and_oven_standby_units(self):
        self.assertAlmostEqual(celsius(0, "°F", -40), -17.7777777778)
        self.assertIsNone(celsius(0, "°F"))
        self.assertFalse(auxiliary_active("1.3", {"unit_of_measurement": "W"}))
        self.assertTrue(auxiliary_active(".2", {"unit_of_measurement": "kW"}))

    def test_bias_requires_independent_reference_and_enough_samples(self):
        model = {}
        for i in range(15):
            estimate({"sensors": {"climate.hp": 23}, "independent_temperature": 21, "thermal_source": "off", "timestamp": 10000 + i * 900}, model)
        value, quality = estimate({"sensors": {"climate.hp": 23}, "temperature": 23, "thermal_source": "off"}, model)
        self.assertEqual(value, 21)
        self.assertEqual(quality, "calibrated_fusion")
        value, quality = estimate({"sensors": {"climate.hp": 23}, "temperature": 23, "thermal_source": "heat_pump"}, model)
        self.assertEqual(quality, "uncalibrated_fallback")

    def test_bias_cannot_train_from_repeated_minute_samples(self):
        model = {}
        for i in range(10):
            estimate({"sensors": {"x": 23}, "independent_temperature": 21, "timestamp": 10000 + i * 60}, model)
        self.assertEqual(model["sensor_bias"]["x:unknown"]["samples"], 1)

    def test_envelope_is_contextual_and_never_claims_cop(self):
        model = {}
        for i in range(20):
            fit_context(model, {"source": "off", "rate": -.3, "temperature": 20, "outdoor": 10, "at": i})
        self.assertAlmostEqual(model["envelope"]["loss_per_hour"], .03)
        self.assertNotIn("cop", model)

    def test_unknown_outdoor_does_not_invent_envelope(self):
        model = {}
        for i in range(20):
            fit_context(model, {"source": "off", "rate": -.3, "temperature": 20, "outdoor": None, "at": i})
        self.assertNotIn("envelope", model)

    def test_horizon_shared_machine_never_duplicates_power(self):
        zones = [{"id": str(i), "config": self.room(), "temperature": 18} for i in range(2)]
        result = optimize({"season": "shoulder"}, zones, {}, NOW, {"thermal_budget_kwh": 2, "hvac_power_limit_w": 1500})
        first = result["steps"][0]
        self.assertEqual(len(first["sources"]), 2)
        self.assertEqual(first["reserved_power_w"], 1400)
        self.assertGreaterEqual(first["remaining_thermal_budget_kwh"], 0)

    def test_unverified_grid_and_hydraulics_never_scheduled(self):
        zone = {"id": "r", "config": {**self.room(), "radiator_entities": ["climate.valve"]}, "temperature": 15}
        result = optimize({"season": "winter"}, [zone], {}, NOW, {"thermal_budget_kwh": 0})
        self.assertTrue(all(not step["sources"] for step in result["steps"]))

    def test_expired_exception_returns_to_routine(self):
        house = {"exception": {"mode": "holiday", "expires_at": (NOW + timedelta(hours=1)).isoformat()}}
        self.assertEqual(target_level(house, self.room(), NOW)[1], "maintenance")
        self.assertEqual(situation(house, NOW + timedelta(hours=2)), "normal")
        self.assertEqual(target_level(house, self.room(), NOW + timedelta(hours=2))[1], "comfort")

    def test_history_retry_idempotent_and_live_stream_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(Path(folder) / "model.json")
            engine.previous = {"live": {"at": 123}}
            points = [{"at": NOW.timestamp() + i * 900, "zones": [{"id": "r", "temperature": 18 + i * .3, "thermal_source": "heat_pump", "contamination": []}]} for i in range(5)]
            engine.import_history({"schema": 1, "points": points})
            before = engine.models["r"]["heat_pump"]["samples"]
            engine.import_history({"schema": 1, "points": points})
            self.assertEqual(engine.models["r"]["heat_pump"]["samples"], before)
            self.assertIn("live", engine.previous)
            self.assertEqual(Engine(engine.path).bootstrap_until, points[-1]["at"])

    def test_slow_quantized_cooling_accumulates(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(Path(folder) / "model.json")
            for i in range(9):
                engine.learn({"id": "r", "temperature": 20 - i * .05, "thermal_source": "off", "contamination": []}, 10000 + i * 900)
            self.assertGreater(engine.models["r"]["off"]["samples"], 0)

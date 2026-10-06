"""Behaviour tests for reconstructed recorder history and DHW scheduling."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dhw", ROOT / "custom_components/casa_es_energy_manager/thermal_history_plan.py")
dhw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dhw)
ENTITIES = {"water": "water_heater.boiler", "heating": "binary_sensor.heat", "boost": "switch.boost", "legionella": "binary_sensor.legionella"}
NOW = datetime(2026, 10, 6, 20, tzinfo=timezone(timedelta(hours=2)))


def row(at, state, temp=None):
    return {"last_updated": at.isoformat(), "state": state,
            "attributes": {"current_temperature": temp} if temp is not None else {}}


def history():
    start = NOW.replace(day=4, hour=0)
    h = {e: [] for e in ENTITIES.values()}
    for role in ("heating", "boost", "legionella"):
        h[ENTITIES[role]] = [row(start, "off")]
    h[ENTITIES["water"]] = [row(start, "GREEN", 53), row(start + timedelta(hours=1), "GREEN", 52),
                               row(start + timedelta(hours=18), "GREEN", 52),
                               row(start + timedelta(hours=18, minutes=10), "GREEN", 44),
                               row(NOW, "GREEN", 44)]
    return h


class HistoryTests(unittest.TestCase):
    def test_rome_dst_days_and_local_draw_bucket(self):
        try:
            rome = ZoneInfo("Europe/Rome")
        except ZoneInfoNotFoundError:
            self.skipTest("tzdata required on Windows")
        start = datetime(2026, 10, 25, 0, tzinfo=rome)
        end = datetime(2026, 10, 26, 0, tzinfo=rome)
        self.assertEqual(end.timestamp() - start.timestamp(), 25 * 3600)
        h = {ENTITIES[role]: [row(start, "off")] for role in ("heating", "boost", "legionella")}
        h[ENTITIES["water"]] = [row(start, "GREEN", 53), row(start.replace(hour=18), "GREEN", 53),
                                row(start.replace(hour=18, minute=10), "GREEN", 45), row(end, "GREEN", 45)]
        model = dhw.reconstruct(h, ENTITIES, end.replace(hour=12))
        self.assertIn("18", model["draw_by_day"]["2026-10-25"])

    def test_passive_cooling_is_not_a_draw(self):
        model = dhw.reconstruct(history(), ENTITIES, NOW)
        self.assertNotIn("1", model["draw_by_day"]["2026-10-04"])
        self.assertGreater(model["draw_by_day"]["2026-10-04"]["18"], 7)

    def test_pdc_start_does_not_invent_a_shower(self):
        h = history()
        h[ENTITIES["heating"]].append(row(NOW.replace(day=4, hour=1, minute=5), "on"))
        model = dhw.reconstruct(h, ENTITIES, NOW)
        self.assertNotIn("1", model["draw_by_day"]["2026-10-04"])

    def test_boost_and_legionella_drops_excluded(self):
        for role in ("boost", "legionella"):
            h = history()
            h[ENTITIES[role]].append(row(NOW.replace(day=4, hour=17), "on"))
            self.assertEqual(dhw.reconstruct(h, ENTITIES, NOW)["draw_by_day"], {})

    def test_today_is_never_a_completed_training_day(self):
        h = history()
        h[ENTITIES["water"]].extend([row(NOW + timedelta(minutes=1), "GREEN", 44), row(NOW + timedelta(minutes=11), "GREEN", 30)])
        model = dhw.reconstruct(h, ENTITIES, NOW + timedelta(minutes=12))
        self.assertNotIn(NOW.date().isoformat(), model["draw_by_day"])

    def test_unknown_states_reset_temperature_baseline(self):
        h = history()
        h[ENTITIES["water"]].insert(3, row(NOW.replace(day=4, hour=18, minute=5), "unavailable"))
        self.assertEqual(dhw.reconstruct(h, ENTITIES, NOW)["draw_by_day"], {})

    def test_fahrenheit_converted_and_nan_rejected(self):
        self.assertAlmostEqual(dhw.temperature(131, "°F"), 55)
        self.assertIsNone(dhw.temperature(float("nan"), "°C"))
        self.assertIsNone(dhw.timestamp("2026-10-06T18:00:00"))

    def test_quantized_unchanged_updates_keep_elapsed_time(self):
        h = history()
        h[ENTITIES["water"]].insert(1, row(NOW.replace(day=4, hour=0, minute=59), "GREEN", 53))
        self.assertNotIn("1", dhw.reconstruct(h, ENTITIES, NOW)["draw_by_day"]["2026-10-04"])

    def test_utc_grouping_uses_local_hour(self):
        h = history()
        for rows in h.values():
            for r in rows:
                r["last_updated"] = datetime.fromisoformat(r["last_updated"]).astimezone(timezone.utc).isoformat()
        self.assertIn("18", dhw.reconstruct(h, ENTITIES, NOW)["draw_by_day"]["2026-10-04"])


class PlanTests(unittest.TestCase):
    def model(self):
        return {"draw_by_day": {"2026-09-29": {"18": 8}, "2026-09-22": {"18": 10}, "2026-09-15": {"18": 20}},
                "green_c_per_h": 2, "standby_loss_c_per_h": 0.5}

    def test_green_starts_hours_before_demand(self):
        now = NOW.replace(hour=13)
        result = dhw.plan(self.model(), now, 43, 53, 65)
        self.assertTrue(result["green_due"])
        self.assertGreater(result["green_lead_hours"], 5)
        self.assertEqual(result["green_target_c"], 53)

    def test_limits_and_reserve_are_explicit(self):
        result = dhw.plan(self.model(), NOW.replace(hour=12), 40, 53, 60)
        self.assertLessEqual(result["target_c"], 60)
        self.assertGreaterEqual(result["reserve_c"], 4.5)
        self.assertGreater(result["capacity_shortfall_c"], 0)
        self.assertGreater(result["green_shortfall_c"], 0)

    def test_tomorrow_plan_and_bootstrap_label(self):
        result = dhw.plan({}, NOW, 40, 53, 65)
        self.assertTrue(result["bootstrap"])
        self.assertEqual(len(result["tomorrow_hourly_draw_c"]), 24)
        self.assertIn("2026-10-07", result["deadline"])

    def test_repeated_draws_same_hour_sum_before_forecast(self):
        m = {"draw_by_day": {"2026-10-04": {"18": 12}, "2026-10-05": {"18": 8}}}
        hourly, _ = dhw.forecast(m, NOW)
        self.assertAlmostEqual(hourly[18], 10)

    def test_current_hour_consumption_is_not_forecast_twice(self):
        m = self.model()
        m["today_draw"] = {"18": 30}
        result = dhw.plan(m, NOW.replace(hour=18), 43, 53, 65)
        self.assertEqual(result["expected_remaining_draw_c"], 0)


if __name__ == "__main__":
    unittest.main()

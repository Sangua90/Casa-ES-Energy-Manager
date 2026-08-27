"""Regression tests for v1.4.4 guarantees retained by later releases."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


def _load_plain_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, COMPONENT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


daily_policy = _load_plain_module("daily_minimum_policy_v144_test", "daily_minimum_policy.py")


class DailyMinimumPolicyTests(unittest.TestCase):
    def test_midnight_does_not_start_five_hour_load_without_solar(self) -> None:
        now = datetime(2026, 8, 27, 0, 7, tzinfo=timezone.utc)
        defer, reason, pressure = daily_policy.should_defer_daily_minimum_start(
            now=now, remaining_minimum_minutes=300, nominal_power_w=30,
            solar_after_house_w=0, pv_potential_after_house_w=0,
        )
        self.assertTrue(defer)
        self.assertFalse(pressure)
        self.assertIn("attende FV", reason)

    def test_available_solar_allows_daily_minimum_load(self) -> None:
        now = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        defer, _, pressure = daily_policy.should_defer_daily_minimum_start(
            now=now, remaining_minimum_minutes=240, nominal_power_w=30,
            solar_after_house_w=50, pv_potential_after_house_w=50,
        )
        self.assertFalse(defer)
        self.assertFalse(pressure)

    def test_deadline_pressure_releases_deferment(self) -> None:
        now = datetime(2026, 8, 27, 19, 0, tzinfo=timezone.utc)
        defer, _, pressure = daily_policy.should_defer_daily_minimum_start(
            now=now, remaining_minimum_minutes=300, nominal_power_w=30,
            solar_after_house_w=0, pv_potential_after_house_w=0,
        )
        self.assertFalse(defer)
        self.assertTrue(pressure)

    def test_custom_end_before_is_daily_deadline(self) -> None:
        now = datetime(2026, 8, 27, 17, 0, tzinfo=timezone.utc)
        defer, _, pressure = daily_policy.should_defer_daily_minimum_start(
            now=now, remaining_minimum_minutes=270, nominal_power_w=30,
            solar_after_house_w=0, pv_potential_after_house_w=0, end_before="22:00:00",
        )
        self.assertFalse(defer)
        self.assertTrue(pressure)


class RuntimePersistenceContractTests(unittest.TestCase):
    def test_v144_storage_layer_is_still_present(self) -> None:
        source = (COMPONENT / "coordinator_v144.py").read_text(encoding="utf-8")
        self.assertIn("from homeassistant.helpers.storage import Store", source)
        self.assertIn("runtime_seconds", source)
        self.assertIn("runtime_activations", source)
        self.assertIn("runtime_previous", source)
        self.assertIn("await self._runtime_store.async_load()", source)
        self.assertIn("await self._runtime_store.async_save(payload)", source)
        self.assertIn('stored.get("date") != today.isoformat()', source)

    def test_v153_retains_v152_v151_v15_and_v144_chain(self) -> None:
        source15 = (COMPONENT / "coordinator_v15.py").read_text(encoding="utf-8")
        source151 = (COMPONENT / "coordinator_v151.py").read_text(encoding="utf-8")
        source152 = (COMPONENT / "coordinator_v152.py").read_text(encoding="utf-8")
        source153 = (COMPONENT / "coordinator_v153.py").read_text(encoding="utf-8")
        init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .coordinator_v144 import CasaESEnergyCoordinator as V144Coordinator", source15)
        self.assertIn("class CasaESEnergyCoordinator(V144Coordinator)", source15)
        self.assertIn("from .coordinator_v15 import CasaESEnergyCoordinator as V15Coordinator", source151)
        self.assertIn("class CasaESEnergyCoordinator(V15Coordinator)", source151)
        self.assertIn("from .coordinator_v151 import CasaESEnergyCoordinator as V151Coordinator", source152)
        self.assertIn("class CasaESEnergyCoordinator(V151Coordinator)", source152)
        self.assertIn("from .coordinator_v152 import CasaESEnergyCoordinator as V152Coordinator", source153)
        self.assertIn("class CasaESEnergyCoordinator(V152Coordinator)", source153)
        self.assertIn("from .coordinator_v153 import CasaESEnergyCoordinator", init_source)

    def test_v152_persists_wall_clock_transitions_and_reconciles_downtime(self) -> None:
        source = (COMPONENT / "coordinator_v152.py").read_text(encoding="utf-8")
        self.assertIn("temporal_state", source)
        self.assertIn("last_real_transition_at", source)
        self.assertIn("_startup_prior_active", source)
        self.assertIn("_downtime_runtime_added_seconds", source)
        self.assertIn("prior_active is True and running_now", source)
        self.assertIn("persistent_real_transition", source)

    def test_version_is_153(self) -> None:
        manifest = (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        const = (COMPONENT / "const.py").read_text(encoding="utf-8")
        self.assertIn('"version": "1.5.3"', manifest)
        self.assertIn('VERSION = "1.5.3"', const)


if __name__ == "__main__":
    unittest.main()

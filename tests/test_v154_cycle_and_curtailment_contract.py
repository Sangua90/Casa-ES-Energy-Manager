"""Regression contracts for Casa ES Energy Manager v1.5.4 retained by later releases."""

from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V154CycleAndCurtailmentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (COMPONENT / "coordinator_v154.py").read_text(encoding="utf-8")

    def test_activation_counter_uses_entity_edges_not_power_edges(self) -> None:
        self.assertIn('ACTIVATION_COUNTER_MODE = "entity_off_to_on_v154"', self.source)
        self.assertIn("previous_active", self.source)
        self.assertIn("entity_active and not previous_active", self.source)
        self.assertIn('item["activation_counter_source"] = ACTIVATION_COUNTER_MODE', self.source)

    def test_legacy_impossible_climate_counts_are_repaired_once(self) -> None:
        self.assertIn("_repair_legacy_activation_counts", self.source)
        self.assertIn("math.ceil(runtime_min / min_on)", self.source)
        self.assertIn("activation_counter_mode", self.source)
        self.assertIn("_v154_activation_store_marked", self.source)

    def test_runtime_still_uses_measured_running_power(self) -> None:
        self.assertIn("if running and elapsed > 0", self.source)
        self.assertIn("_runtime_seconds", self.source)

    def test_near_target_curtailment_can_release_small_loads(self) -> None:
        self.assertIn("CURTAILMENT_NEAR_TARGET_PROBE_MAX_W = 300.0", self.source)
        self.assertIn("pv_curtailment_likely", self.source)
        self.assertIn("curtailment_harvest_start", self.source)
        self.assertIn("CURTAILMENT_HARVEST_RESERVE_W", self.source)

    def test_large_loads_below_target_need_measured_surplus(self) -> None:
        self.assertIn("not target_reached", self.source)
        self.assertIn("measured_surplus_w", self.source)
        self.assertIn("power_w > CURTAILMENT_NEAR_TARGET_PROBE_MAX_W", self.source)

    def test_v154_remains_in_release_chain(self) -> None:
        source155 = (COMPONENT / "coordinator_v155.py").read_text(encoding="utf-8")
        self.assertIn("coordinator_v154", source155)
        self.assertIn("V154Coordinator", source155)


if __name__ == "__main__":
    unittest.main()

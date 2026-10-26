"""Regression contracts for Casa ES Energy Manager v1.5.7."""

from pathlib import Path
import unittest
from release_contract import release_chain_source, assert_release_version

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "casa_es_energy_manager"


class V157SelectPauseResumeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flow = (COMPONENT / "monitored_load_flow_v157.py").read_text(encoding="utf-8")
        self.coordinator = (COMPONENT / "coordinator_v157.py").read_text(encoding="utf-8")

    def test_ui_exposes_single_select_pause_resume_mode(self) -> None:
        self.assertIn('MONITORED_EMERGENCY_MODE_SELECT_PAUSE_RESUME = "select_pause_resume"', self.flow)
        self.assertIn('"Menu a tendina Pausa + Riprendi (select)"', self.flow)
        self.assertIn('EntitySelectorConfig(domain="select")', self.flow)
        self.assertIn('self._pending[CONF_MONITORED_LOAD_RESUME_ENTITY] = entity_id', self.flow)

    def test_runtime_uses_select_option_not_turn_on_off(self) -> None:
        self.assertIn('domain != "select"', self.coordinator)
        self.assertIn('"select",\n            "select_option"', self.coordinator)
        self.assertIn('PAUSE_ALIASES', self.coordinator)
        self.assertIn('RESUME_ALIASES', self.coordinator)
        self.assertNotIn('"stop"', self.coordinator.split('PAUSE_ALIASES', 1)[1].split('def _norm', 1)[0])

    def test_release_is_v157(self) -> None:
        init_source = release_chain_source(COMPONENT)
        subentry = (COMPONENT / "subentry_support.py").read_text(encoding="utf-8")
        manifest = (COMPONENT / "manifest.json").read_text(encoding="utf-8")
        const = (COMPONENT / "const.py").read_text(encoding="utf-8")
        self.assertIn("coordinator_v157", init_source)
        self.assertIn("monitored_load_flow_v157", subentry)
        assert_release_version(self, COMPONENT, "1.5.7")


if __name__ == "__main__":
    unittest.main()


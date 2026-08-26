"""Regression tests for Casa ES UI translations and subentry contracts."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "casa_es_energy_manager"


class TestUIContract(unittest.TestCase):
    """Keep the HACS UI contract explicit and regression-safe."""

    def setUp(self) -> None:
        self.strings = json.loads(
            (INTEGRATION / "strings.json").read_text(encoding="utf-8")
        )
        self.it = json.loads(
            (INTEGRATION / "translations" / "it.json").read_text(encoding="utf-8")
        )
        self.en = json.loads(
            (INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8")
        )

    def test_hacs_translation_files_exist_and_match_source(self) -> None:
        """Custom integrations need runtime translations, not only strings.json."""
        self.assertEqual(self.strings, self.it)
        self.assertEqual(self.strings, self.en)

    def test_every_visible_form_field_has_description(self) -> None:
        """No user-facing field may be left without explanatory text."""
        groups = [
            self.it["config"]["step"],
            self.it["options"]["step"],
        ]
        for subentry in self.it["config_subentries"].values():
            groups.append(subentry.get("step", {}))

        for group in groups:
            for step_name, step in group.items():
                fields = step.get("data", {})
                if not fields:
                    continue
                descriptions = step.get("data_description", {})
                self.assertEqual(
                    set(fields),
                    set(descriptions),
                    f"Descrizioni incomplete nello step {step_name}",
                )
                for key, value in descriptions.items():
                    self.assertTrue(
                        str(value).strip(),
                        f"Descrizione vuota per {step_name}.{key}",
                    )

    def test_optional_fields_are_visibly_marked(self) -> None:
        """Optional fields requested by the user must contain an asterisk."""
        config_optional = {
            "phase_l1_power_sensor",
            "phase_l2_power_sensor",
            "phase_l3_power_sensor",
            "pv_potential_power_sensor",
            "pv_forecast_remaining_today_sensor",
            "pv_forecast_current_hour_sensor",
            "pv_forecast_next_hour_sensor",
            "pv_forecast_today_sensor",
            "pv_forecast_tomorrow_sensor",
            "weather_entity",
            "emergency_charge_start_script",
            "emergency_charge_stop_script",
            "extra_context_sensors",
            "ai_task_entity",
        }
        managed_optional = {
            "power_sensor",
            "expected_runtime_minutes",
            "min_on_minutes",
            "min_off_minutes",
            "schedule_deadline",
            "start_after",
            "end_before",
        }
        monitored_optional = {"emergency_entity", "resume_entity"}

        all_config_labels: dict[str, str] = {}
        for step in self.it["config"]["step"].values():
            all_config_labels.update(step.get("data", {}))
        for key in config_optional:
            self.assertIn("*", all_config_labels[key], key)

        all_managed_labels: dict[str, str] = {}
        for step in self.it["config_subentries"]["managed_device"]["step"].values():
            all_managed_labels.update(step.get("data", {}))
        for key in managed_optional:
            self.assertIn("*", all_managed_labels[key], key)

        all_monitored_labels: dict[str, str] = {}
        for step in self.it["config_subentries"]["monitored_load"]["step"].values():
            all_monitored_labels.update(step.get("data", {}))
        for key in monitored_optional:
            self.assertIn("*", all_monitored_labels[key], key)

    def test_add_actions_are_unambiguous(self) -> None:
        subentries = self.it["config_subentries"]
        managed = subentries["managed_device"]["initiate_flow"]["user"].lower()
        monitored = subentries["monitored_load"]["initiate_flow"]["user"].lower()
        self.assertNotEqual(managed, monitored)
        self.assertIn("gestito", managed)
        self.assertIn("controllabile", managed)
        self.assertIn("monitorato", monitored)
        self.assertIn("protezione", subentries["monitored_load"]["entry_type"].lower())

    def test_managed_flow_has_conditional_climate_step_without_ev_or_dependency(self) -> None:
        managed_steps = self.it["config_subentries"]["managed_device"]["step"]
        monitored_steps = self.it["config_subentries"]["monitored_load"]["step"]
        self.assertTrue(
            {"user", "reconfigure", "climate", "constraints"} <= set(managed_steps)
        )
        self.assertNotIn("advanced", managed_steps)
        self.assertTrue({"user", "reconfigure"} <= set(monitored_steps))

        all_fields: set[str] = set()
        for step in managed_steps.values():
            all_fields.update(step.get("data", {}))
        removed = {
            "requires_entity",
            "dynamic_current",
            "current_entity",
            "min_current_a",
            "max_current_a",
            "ev_soc_sensor",
            "ev_connected_sensor",
            "ev_target_soc",
        }
        self.assertTrue(removed.isdisjoint(all_fields))
        self.assertIn("device_type", all_fields)
        self.assertIn("mode_climate_entity", all_fields)

        managed_source = (INTEGRATION / "managed_device_flow_v1.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('step_id="user"', managed_source)
        self.assertIn("async_step_climate", managed_source)
        self.assertNotIn("async_step_advanced", managed_source)

    def test_monitored_load_keeps_one_category_with_optional_emergency_control(self) -> None:
        monitored = self.it["config_subentries"]["monitored_load"]
        for step_name in ("user", "reconfigure"):
            fields = monitored["step"][step_name]["data"]
            self.assertIn("name", fields)
            self.assertIn("power_sensor", fields)
            self.assertIn("phase", fields)
            self.assertIn("enabled", fields)
            self.assertIn("emergency_entity", fields)
            self.assertIn("resume_entity", fields)
        description = monitored["step"]["user"]["description"].lower()
        self.assertIn("emergenza", description)
        self.assertIn("mai", description)
        self.assertIn("batteria", description)

        source = (INTEGRATION / "monitored_load_flow.py").read_text(encoding="utf-8")
        self.assertIn("CONF_MONITORED_LOAD_EMERGENCY_ENTITY", source)
        self.assertIn("CONF_MONITORED_LOAD_RESUME_ENTITY", source)

    def test_climate_device_type_is_clear_and_mode_reference_is_required(self) -> None:
        managed = self.it["config_subentries"]["managed_device"]["step"]
        self.assertIn("Tipo di dispositivo", managed["user"]["data"]["device_type"])
        selector_options = self.it["selector"]["managed_device_type"]["options"]
        self.assertIn("climate", selector_options)
        self.assertIn("pompa di calore", selector_options["climate"].lower())
        self.assertIn("mode_climate_entity", managed["climate"]["data"])
        self.assertNotIn("*", managed["climate"]["data"]["mode_climate_entity"])

    def test_nominal_power_is_explained_as_estimate_and_fallback(self) -> None:
        managed = self.it["config_subentries"]["managed_device"]["step"]["user"]
        label = managed["data"]["nominal_power_w"].lower()
        description = managed["data_description"]["nominal_power_w"].lower()
        self.assertIn("fallback", label)
        self.assertIn("iniziale", description)
        self.assertIn("apprendimento", description)

    def test_priority_and_optional_cycle_contract(self) -> None:
        managed = self.it["config_subentries"]["managed_device"]["step"]["user"]
        self.assertIn("1-100", managed["data"]["priority"])
        self.assertIn("1 a 100", managed["data_description"]["priority"])
        for key in (
            "expected_runtime_minutes",
            "min_on_minutes",
            "min_off_minutes",
        ):
            self.assertIn("*", managed["data"][key])
            self.assertIn("Facoltativo", managed["data_description"][key])

    def test_real_control_master_and_v14_emergency_controller_exist(self) -> None:
        const_text = (INTEGRATION / "const.py").read_text(encoding="utf-8")
        init_text = (INTEGRATION / "__init__.py").read_text(encoding="utf-8")
        switch_text = (INTEGRATION / "switch.py").read_text(encoding="utf-8")
        coordinator_text = (INTEGRATION / "coordinator_v14.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("DEFAULT_AUTOMATIC_REAL_LOAD_CONTROL = False", const_text)
        self.assertIn("Platform.SWITCH", init_text)
        self.assertIn("coordinator_v14", init_text)
        self.assertIn("Controllo automatico reale", switch_text)
        self.assertIn("_async_apply_real_control", coordinator_text)
        self.assertIn("monitored_emergency_control", coordinator_text)
        self.assertIn("grid_total", coordinator_text)
        self.assertIn("phase_or_inverter", coordinator_text)

    def test_manifest_and_const_versions_match(self) -> None:
        manifest = json.loads(
            (INTEGRATION / "manifest.json").read_text(encoding="utf-8")
        )
        const_text = (INTEGRATION / "const.py").read_text(encoding="utf-8")
        match = re.search(r'^VERSION = "([^"]+)"$', const_text, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(manifest["version"], match.group(1))
        self.assertEqual("1.4.0", manifest["version"])


if __name__ == "__main__":
    unittest.main()

"""v1 AI planner context extensions."""

from __future__ import annotations

from typing import Any

from .ai_planner import CasaESAIPlanner as BasePlanner


class CasaESAIPlanner(BasePlanner):
    """Use the coordinator's fresh deterministic policy and learned context."""

    async def _planner_context(self) -> dict[str, Any]:
        context = await super()._planner_context()
        data = self.coordinator.data or {}
        fresh_policy = data.get("planner_policy")
        if isinstance(fresh_policy, dict):
            context["policy"] = fresh_policy
            context["energy_preference"] = fresh_policy.get("energy_preference")
            for context_key, policy_key in (
                ("hours_to_target", "hours_to_target"),
                ("battery_energy_needed_kwh", "battery_energy_needed_kwh"),
                ("battery_input_energy_needed_kwh", "battery_input_energy_needed_kwh"),
                ("expected_base_load_w", "expected_base_load_w"),
                ("base_load_energy_to_target_kwh", "base_load_energy_to_target_kwh"),
                ("battery_charge_efficiency_pct", "battery_charge_efficiency_pct"),
            ):
                context[context_key] = fresh_policy.get(policy_key)

        # The base planner historically jumped to tomorrow as soon as the target
        # hour passed. From v1.4.2 the coordinator owns the daily target window:
        # after the configured deadline the same SOC target stays active until
        # midnight, then a new daily cycle starts.
        try:
            _, effective_target = self.coordinator._target_time()
            context["target_time"] = effective_target.isoformat()
        except Exception:
            pass
        context["battery_target_mode"] = data.get("battery_target_mode")
        context["battery_target_deadline"] = data.get("battery_target_deadline")
        context["battery_target_effective_planning_target"] = data.get(
            "battery_target_effective_planning_target"
        )
        context["battery_target_recovery_until_midnight"] = data.get(
            "battery_target_recovery_until_midnight"
        )

        context["managed_device_modes"] = [
            {
                "name": item.get("name"),
                "entity_id": item.get("entity_id"),
                "mode": item.get("management_mode"),
                "adaptive_profile": item.get("adaptive_profile"),
            }
            for item in (data.get("managed_device_configs") or [])
        ]
        context["adaptive_power_profiles"] = data.get("adaptive_power_profiles") or {}
        context["phase_load_breakdown"] = data.get("phase_load_breakdown") or []
        context["emergency_charge_active"] = data.get("emergency_charge_active")
        return context

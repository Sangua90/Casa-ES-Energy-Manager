"""v1 AI planner context extensions."""

from __future__ import annotations

from typing import Any

from .ai_planner import CasaESAIPlanner as BasePlanner


class CasaESAIPlanner(BasePlanner):
    """Use the coordinator's fresh v1 deterministic policy and learned context."""

    async def _planner_context(self) -> dict[str, Any]:
        context = await super()._planner_context()
        data = self.coordinator.data or {}
        fresh_policy = data.get("planner_policy")
        if isinstance(fresh_policy, dict):
            context["policy"] = fresh_policy
            context["energy_preference"] = fresh_policy.get("energy_preference")
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

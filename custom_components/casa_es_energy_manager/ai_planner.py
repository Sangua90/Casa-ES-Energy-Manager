"""Optional AI planning advisor for Casa ES Energy Manager."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AI_ENABLED,
    CONF_AI_INTERVAL_MINUTES,
    CONF_AI_TASK_ENTITY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
    CONF_BATTERY_TARGET_HOUR,
    CONF_BATTERY_TARGET_SOC,
    CONF_EXPECTED_BASE_LOAD_W,
    DEFAULT_AI_ENABLED,
    DEFAULT_AI_INTERVAL_MINUTES,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
    DEFAULT_BATTERY_TARGET_HOUR,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_EXPECTED_BASE_LOAD_W,
)
from .planner_policy import apply_ai_guardrails, build_planner_policy

_LOGGER = logging.getLogger(__name__)

ALLOWED_STRATEGIES = {
    "battery_first",
    "balanced",
    "use_surplus",
    "protect_grid",
    "grid_charge",
    "insufficient_data",
}


class CasaESAIPlanner:
    """Ask an AI Task entity for a read-only energy strategy."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._running = False

    def _config(self, key: str, default: Any = None) -> Any:
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    @property
    def enabled(self) -> bool:
        return bool(self._config(CONF_AI_ENABLED, DEFAULT_AI_ENABLED))

    @property
    def interval(self) -> timedelta:
        minutes = int(self._config(CONF_AI_INTERVAL_MINUTES, DEFAULT_AI_INTERVAL_MINUTES))
        return timedelta(minutes=max(15, minutes))

    @property
    def ai_task_entity(self) -> str | None:
        value = self._config(CONF_AI_TASK_ENTITY)
        return str(value) if value else None

    async def _weather_forecast(self, weather_entity: str | None) -> list[dict[str, Any]]:
        """Fetch a short hourly weather forecast when supported."""
        if not weather_entity:
            return []
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception as err:
            _LOGGER.debug("Weather forecast unavailable for AI context: %s", err)
            return []
        if not isinstance(response, dict):
            return []
        entity_result = response.get(weather_entity)
        if not isinstance(entity_result, dict):
            return []
        forecast = entity_result.get("forecast")
        if not isinstance(forecast, list):
            return []
        return [item for item in forecast[:6] if isinstance(item, dict)]

    async def _planner_context(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        capacity_kwh = float(self._config(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH))
        target_soc = float(self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC))
        target_hour = int(self._config(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR))
        expected_base_load_w = float(
            self._config(CONF_EXPECTED_BASE_LOAD_W, DEFAULT_EXPECTED_BASE_LOAD_W)
        )
        charge_efficiency_pct = float(
            self._config(
                CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
                DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
            )
        )

        now = dt_util.now()
        target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        policy = build_planner_policy(
            data,
            now=now,
            target=target,
            battery_capacity_kwh=capacity_kwh,
            battery_target_soc=target_soc,
            expected_base_load_w=expected_base_load_w,
            battery_charge_efficiency_pct=charge_efficiency_pct,
        )

        weather_entity = data.get("weather_entity")
        weather_forecast = await self._weather_forecast(
            str(weather_entity) if weather_entity else None
        )

        return {
            "now": now.isoformat(),
            "target_time": target.isoformat(),
            "hours_to_target": policy["hours_to_target"],
            "battery_capacity_kwh": capacity_kwh,
            "battery_target_soc": target_soc,
            "battery_energy_needed_kwh": policy["battery_energy_needed_kwh"],
            "battery_input_energy_needed_kwh": policy["battery_input_energy_needed_kwh"],
            "expected_base_load_w": policy["expected_base_load_w"],
            "base_load_energy_to_target_kwh": policy["base_load_energy_to_target_kwh"],
            "battery_charge_efficiency_pct": policy["battery_charge_efficiency_pct"],
            "pv_measured_power_w": data.get("pv_power_w"),
            "pv_potential_power_w": data.get("pv_potential_w"),
            "pv_potential_gap_w": data.get("pv_potential_gap_w"),
            "pv_potential_after_house_w": data.get("pv_potential_after_house_w"),
            "pv_curtailment_likely": data.get("pv_curtailment_likely"),
            "load_power_w": data.get("load_power_w"),
            "grid_import_w": data.get("grid_import_w"),
            "battery_soc": data.get("battery_soc"),
            "battery_charge_w": data.get("battery_charge_w"),
            "battery_discharge_w": data.get("battery_discharge_w"),
            "solar_after_house_measured_w": data.get("solar_after_house_w"),
            "grid_headroom_w": data.get("grid_headroom_w"),
            "inverter_headroom_w": data.get("inverter_headroom_w"),
            "phase_l1_headroom_w": data.get("phase_l1_headroom_w"),
            "phase_l2_headroom_w": data.get("phase_l2_headroom_w"),
            "phase_l3_headroom_w": data.get("phase_l3_headroom_w"),
            "manager_status": data.get("status"),
            "forecast_remaining_today_kwh": data.get("forecast_remaining_kwh"),
            "forecast_current_hour_power_w": data.get("forecast_current_hour_power_w"),
            "forecast_current_hour_energy_kwh": data.get("forecast_current_hour_kwh"),
            "forecast_next_hour_power_w": data.get("forecast_next_hour_power_w"),
            "forecast_next_hour_energy_kwh": data.get("forecast_next_hour_kwh"),
            "forecast_today_kwh": data.get("forecast_today_kwh"),
            "forecast_tomorrow_kwh": data.get("forecast_tomorrow_kwh"),
            "forecast_power_curve": data.get("forecast_curve") or [],
            "weather_current": data.get("weather_current"),
            "weather_hourly_next_6": weather_forecast,
            "extra_context_sensors": data.get("extra_context") or [],
            "policy": policy,
        }

    def _instructions(self, context: dict[str, Any]) -> str:
        """Build a compact prompt. The AI advises; it never executes actions."""
        return (
            "Sei il planner energetico consultivo di Casa ES. Non puoi comandare dispositivi, "
            "inverter o ricarica rete. Proponi una strategia per i prossimi 30 minuti usando "
            "solo i dati forniti. La sicurezza locale ha sempre priorità. Non inventare dati. "
            "La sezione policy è calcolata deterministicamente ed è VINCOLANTE. "
            "forecast_margin_after_base_load_kwh sottrae dal FV previsto sia l'energia richiesta "
            "alla batteria, corretta per efficienza, sia il consumo base previsto della casa. "
            "flexible_energy_budget_kwh è il budget energetico prudente residuo per futuri carichi "
            "flessibili dopo un buffer di sicurezza: usalo come riferimento energetico principale. "
            "Puoi scegliere protect_grid SOLO se policy.protect_grid_allowed=true. Puoi scegliere "
            "grid_charge o consigliarlo SOLO se policy.grid_charge_allowed=true. Se "
            "policy.protect_grid_required=true devi scegliere protect_grid e vietare carichi "
            "flessibili. Non dire che il FV è assente a meno che policy.solar_state=absent. "
            "Se policy.target_reachability=definite_shortfall non consentire carichi flessibili. "
            "Se policy.battery_first_preferred=true considera battery_first. Se manca un dato "
            "essenziale usa insufficient_data. Obiettivi: 1) sicurezza; 2) target batteria; "
            "3) massimo autoconsumo FV; 4) carichi flessibili. reason in italiano, max 180 caratteri.\n\n"
            f"Dati correnti: {context}"
        )

    @staticmethod
    def _structure() -> dict[str, Any]:
        return {
            "strategy": {
                "description": "Una tra battery_first, balanced, use_surplus, protect_grid, grid_charge, insufficient_data",
                "required": True,
                "selector": {"text": {}},
            },
            "allow_flexible_loads": {
                "description": "Se nei prossimi 30 minuti è sensato consentire carichi flessibili",
                "required": True,
                "selector": {"boolean": {}},
            },
            "grid_charge_recommended": {
                "description": "True solo se policy.grid_charge_allowed=true",
                "required": True,
                "selector": {"boolean": {}},
            },
            "battery_reserve_w": {
                "description": "Potenza FV consultiva da riservare alla batteria, 0-10000 W",
                "required": True,
                "selector": {"number": {"min": 0, "max": 10000, "mode": "box"}},
            },
            "confidence": {
                "description": "Confidenza della raccomandazione da 0 a 100",
                "required": True,
                "selector": {"number": {"min": 0, "max": 100, "mode": "box"}},
            },
            "reason": {
                "description": "Motivazione sintetica in italiano, massimo 180 caratteri",
                "required": True,
                "selector": {"text": {}},
            },
        }

    async def async_refresh(self, *_: Any) -> None:
        """Refresh the AI recommendation."""
        if not self.enabled:
            self.coordinator.update_ai_data(
                {"ai_status": "disabled", "ai_strategy": None, "ai_reason": None}
            )
            return

        entity_id = self.ai_task_entity
        if not entity_id:
            self.coordinator.update_ai_data(
                {
                    "ai_status": "not_configured",
                    "ai_strategy": None,
                    "ai_reason": "Selezionare un'entità AI Task nelle opzioni.",
                }
            )
            return

        if self._running:
            return

        self._running = True
        self.coordinator.update_ai_data({"ai_status": "running", "ai_error": None})

        try:
            context = await self._planner_context()
            policy = context["policy"]
            response = await self.hass.services.async_call(
                "ai_task",
                "generate_data",
                {
                    "entity_id": entity_id,
                    "task_name": "Casa ES energy strategy",
                    "instructions": self._instructions(context),
                    "structure": self._structure(),
                },
                blocking=True,
                return_response=True,
            )

            generated = response.get("data") if isinstance(response, dict) else None
            if not isinstance(generated, dict):
                raise ValueError("AI Task did not return structured data")

            raw_strategy = str(generated.get("strategy", "insufficient_data")).strip()
            if raw_strategy not in ALLOWED_STRATEGIES:
                generated = dict(generated)
                generated["strategy"] = "insufficient_data"

            guarded = apply_ai_guardrails(generated, policy)
            raw_reason = str(generated.get("reason", "")).strip()[:180]
            final_reason = guarded["guardrail_reason"] or raw_reason
            reserve_w = _bounded_float(generated.get("battery_reserve_w"), 0, 10000)
            confidence = _bounded_float(generated.get("confidence"), 0, 100)
            if guarded["guardrail_applied"]:
                confidence = min(confidence, 70.0)

            self.coordinator.update_ai_data(
                {
                    "ai_status": "ok",
                    "ai_strategy": guarded["strategy"],
                    "ai_raw_strategy": guarded["raw_strategy"],
                    "ai_allow_flexible_loads": guarded["allow_flexible_loads"],
                    "ai_grid_charge_recommended": guarded["grid_charge_recommended"],
                    "ai_battery_reserve_w": reserve_w,
                    "ai_confidence": confidence,
                    "ai_reason": final_reason,
                    "ai_raw_reason": raw_reason,
                    "ai_guardrail_applied": guarded["guardrail_applied"],
                    "ai_guardrail_reason": guarded["guardrail_reason"],
                    "ai_last_update": dt_util.now().isoformat(),
                    "ai_error": None,
                    "ai_context": context,
                    "ai_policy": policy,
                    "ai_raw_result": generated,
                    "battery_energy_needed_kwh": policy["battery_energy_needed_kwh"],
                    "battery_input_energy_needed_kwh": policy["battery_input_energy_needed_kwh"],
                    "base_load_energy_to_target_kwh": policy["base_load_energy_to_target_kwh"],
                    "forecast_energy_to_target_kwh": policy["forecast_energy_to_target_kwh"],
                    "forecast_margin_before_base_load_kwh": policy["forecast_margin_before_base_load_kwh"],
                    "forecast_margin_after_base_load_kwh": policy["forecast_margin_after_base_load_kwh"],
                    "flexible_energy_budget_kwh": policy["flexible_energy_budget_kwh"],
                    "planner_target_reachability": policy["target_reachability"],
                    "planner_grid_pressure": policy["grid_pressure"],
                    "planner_solar_state": policy["solar_state"],
                }
            )
        except Exception as err:
            _LOGGER.warning("AI planner update failed: %s", err)
            self.coordinator.update_ai_data(
                {
                    "ai_status": "error",
                    "ai_error": str(err),
                    "ai_last_update": dt_util.now().isoformat(),
                }
            )
        finally:
            self._running = False


def _bounded_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, number))

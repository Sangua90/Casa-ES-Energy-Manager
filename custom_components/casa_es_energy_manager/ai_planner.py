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
    CONF_BATTERY_TARGET_HOUR,
    CONF_BATTERY_TARGET_SOC,
    DEFAULT_AI_ENABLED,
    DEFAULT_AI_INTERVAL_MINUTES,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_TARGET_HOUR,
    DEFAULT_BATTERY_TARGET_SOC,
)

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

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: Any,
    ) -> None:
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
        minutes = int(
            self._config(CONF_AI_INTERVAL_MINUTES, DEFAULT_AI_INTERVAL_MINUTES)
        )
        return timedelta(minutes=max(15, minutes))

    @property
    def ai_task_entity(self) -> str | None:
        value = self._config(CONF_AI_TASK_ENTITY)
        return str(value) if value else None

    def _planner_context(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        soc = float(data.get("battery_soc") or 0.0)
        capacity_kwh = float(
            self._config(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        )
        target_soc = float(
            self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC)
        )
        target_hour = int(
            self._config(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR)
        )

        energy_needed_kwh = max(target_soc - soc, 0.0) / 100.0 * capacity_kwh

        now = dt_util.now()
        target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        hours_to_target = max((target - now).total_seconds() / 3600.0, 0.0)

        return {
            "now": now.isoformat(),
            "target_time": target.isoformat(),
            "hours_to_target": round(hours_to_target, 2),
            "battery_capacity_kwh": capacity_kwh,
            "battery_target_soc": target_soc,
            "battery_energy_needed_kwh": round(energy_needed_kwh, 3),
            "pv_power_w": data.get("pv_power_w"),
            "load_power_w": data.get("load_power_w"),
            "grid_import_w": data.get("grid_import_w"),
            "battery_soc": data.get("battery_soc"),
            "battery_charge_w": data.get("battery_charge_w"),
            "battery_discharge_w": data.get("battery_discharge_w"),
            "solar_after_house_w": data.get("solar_after_house_w"),
            "grid_headroom_w": data.get("grid_headroom_w"),
            "inverter_headroom_w": data.get("inverter_headroom_w"),
            "phase_l1_headroom_w": data.get("phase_l1_headroom_w"),
            "phase_l2_headroom_w": data.get("phase_l2_headroom_w"),
            "phase_l3_headroom_w": data.get("phase_l3_headroom_w"),
            "manager_status": data.get("status"),
            "forecast_remaining_kwh": data.get("forecast_remaining_kwh"),
        }

    def _instructions(self, context: dict[str, Any]) -> str:
        """Build a compact prompt. The AI advises; it never executes actions."""
        return (
            "Sei il planner energetico consultivo di Casa ES. "
            "Non puoi e non devi comandare dispositivi, inverter o ricarica rete. "
            "Analizza esclusivamente i dati forniti e proponi la strategia energetica "
            "per i prossimi 30 minuti. La sicurezza elettrica e i limiti di fase hanno "
            "sempre priorità. Non inventare dati mancanti o previsioni meteo/FV. "
            "Se manca un dato essenziale, usa strategy=insufficient_data. "
            "Obiettivi in ordine: 1) evitare sovraccarico rete/fasi/inverter; "
            "2) raggiungere il target batteria; 3) massimizzare autoconsumo FV; "
            "4) usare i carichi flessibili solo quando ragionevole. "
            "Il campo reason deve essere in italiano e massimo 180 caratteri.\n\n"
            f"Dati correnti: {context}"
        )

    @staticmethod
    def _structure() -> dict[str, Any]:
        return {
            "strategy": {
                "description": (
                    "Una tra battery_first, balanced, use_surplus, protect_grid, "
                    "grid_charge, insufficient_data"
                ),
                "required": True,
                "selector": {"text": {}},
            },
            "allow_flexible_loads": {
                "description": "Se nei prossimi 30 minuti è sensato consentire carichi flessibili",
                "required": True,
                "selector": {"boolean": {}},
            },
            "grid_charge_recommended": {
                "description": (
                    "Solo consiglio: true se dai dati disponibili sarebbe opportuno "
                    "valutare ricarica batteria da rete"
                ),
                "required": True,
                "selector": {"boolean": {}},
            },
            "battery_reserve_w": {
                "description": (
                    "Potenza FV in watt che suggerisci di riservare alla carica batteria. "
                    "Valore consultivo da 0 a 10000"
                ),
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
                {
                    "ai_status": "disabled",
                    "ai_strategy": None,
                    "ai_reason": None,
                }
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
            context = self._planner_context()
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

            strategy = str(generated.get("strategy", "insufficient_data")).strip()
            if strategy not in ALLOWED_STRATEGIES:
                strategy = "insufficient_data"

            reason = str(generated.get("reason", "")).strip()[:180]
            reserve_w = _bounded_float(generated.get("battery_reserve_w"), 0, 10000)
            confidence = _bounded_float(generated.get("confidence"), 0, 100)

            self.coordinator.update_ai_data(
                {
                    "ai_status": "ok",
                    "ai_strategy": strategy,
                    "ai_allow_flexible_loads": bool(
                        generated.get("allow_flexible_loads", False)
                    ),
                    "ai_grid_charge_recommended": bool(
                        generated.get("grid_charge_recommended", False)
                    ),
                    "ai_battery_reserve_w": reserve_w,
                    "ai_confidence": confidence,
                    "ai_reason": reason,
                    "ai_last_update": dt_util.now().isoformat(),
                    "ai_error": None,
                    "ai_context": context,
                    "ai_raw_result": generated,
                }
            )
        except Exception as err:  # The advisor must never affect local control.
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

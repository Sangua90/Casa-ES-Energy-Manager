"""Coordinator for Casa ES Energy Manager."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .calculations import calculate_metrics
from .const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_EXTRA_CONTEXT_SENSORS,
    CONF_GRID_POWER_LIMIT,
    CONF_GRID_POWER_SENSOR,
    CONF_INVERTER_POWER_LIMIT,
    CONF_LOAD_POWER_SENSOR,
    CONF_PHASE_L1_POWER_SENSOR,
    CONF_PHASE_L2_POWER_SENSOR,
    CONF_PHASE_L3_POWER_SENSOR,
    CONF_PHASE_POWER_LIMIT,
    CONF_PV_FORECAST_CURRENT_HOUR_SENSOR,
    CONF_PV_FORECAST_NEXT_HOUR_SENSOR,
    CONF_PV_FORECAST_REMAINING_TODAY_SENSOR,
    CONF_PV_FORECAST_TODAY_SENSOR,
    CONF_PV_FORECAST_TOMORROW_SENSOR,
    CONF_PV_POTENTIAL_POWER_SENSOR,
    CONF_PV_POWER_SENSOR,
    CONF_SAFETY_MARGIN,
    CONF_WEATHER_ENTITY,
    CURTAILMENT_GRID_IMPORT_MAX_W,
    CURTAILMENT_POTENTIAL_GAP_W,
    CURTAILMENT_SOC_THRESHOLD,
    DEFAULT_GRID_POWER_LIMIT,
    DEFAULT_INVERTER_POWER_LIMIT,
    DEFAULT_PHASE_POWER_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    DOMAIN,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class CasaESEnergyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Read Home Assistant energy sensors and calculate Casa ES metrics."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.ai_planner: Any = None
        self.ai_data: dict[str, Any] = {
            "ai_status": "disabled",
            "ai_strategy": None,
            "ai_reason": None,
            "ai_last_update": None,
        }

    def _config(self, key: str, default: Any = None) -> Any:
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    def _numeric_state(
        self,
        entity_id: str | None,
        *,
        power: bool = False,
        energy: bool = False,
    ) -> float | None:
        """Read one numeric state and normalize power to W or energy to kWh."""
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None

        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None

        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if power:
            if unit == "kW":
                value *= 1_000.0
            elif unit == "MW":
                value *= 1_000_000.0
        elif energy:
            if unit == "Wh":
                value /= 1_000.0
            elif unit == "MWh":
                value *= 1_000.0
        return value

    def _entity_snapshot(self, entity_id: str) -> dict[str, Any]:
        """Return a compact, safe context snapshot for an arbitrary entity."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return {"entity_id": entity_id, "available": False}
        return {
            "entity_id": entity_id,
            "name": state.attributes.get(ATTR_FRIENDLY_NAME, entity_id),
            "state": state.state,
            "unit": state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
            "available": state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE),
        }

    def _forecast_curve(self, entity_id: str | None) -> list[dict[str, Any]]:
        """Read a provider's optional ``watts`` forecast curve attribute.

        Some solar forecast integrations expose a timestamp->watts dictionary on
        their daily energy sensor. We consume it opportunistically without making
        the integration depend on one specific forecast provider.
        """
        if not entity_id:
            return []
        state = self.hass.states.get(entity_id)
        if state is None:
            return []
        raw = state.attributes.get("watts")
        if not isinstance(raw, dict):
            return []

        now_utc = dt_util.utcnow()
        points: list[tuple[Any, float]] = []
        for timestamp, value in raw.items():
            parsed = dt_util.parse_datetime(str(timestamp))
            if parsed is None:
                continue
            parsed = dt_util.as_utc(parsed)
            if parsed < now_utc - timedelta(minutes=5):
                continue
            try:
                power_w = float(value)
            except (TypeError, ValueError):
                continue
            points.append((parsed, max(power_w, 0.0)))

        points.sort(key=lambda item: item[0])
        return [
            {"time": timestamp.isoformat(), "power_w": round(power_w, 1)}
            for timestamp, power_w in points[:24]
        ]

    def update_ai_data(self, values: dict[str, Any]) -> None:
        """Store advisory AI data without affecting deterministic metrics."""
        self.ai_data.update(values)
        if self.data:
            updated = dict(self.data)
            updated.update(self.ai_data)
            self.async_set_updated_data(updated)

    async def _async_update_data(self) -> dict[str, Any]:
        pv = self._numeric_state(self._config(CONF_PV_POWER_SENSOR), power=True)
        load = self._numeric_state(self._config(CONF_LOAD_POWER_SENSOR), power=True)
        grid = self._numeric_state(self._config(CONF_GRID_POWER_SENSOR), power=True)
        battery_soc = self._numeric_state(self._config(CONF_BATTERY_SOC_SENSOR))
        battery = self._numeric_state(self._config(CONF_BATTERY_POWER_SENSOR), power=True)

        required = {
            "PV": pv,
            "load": load,
            "grid": grid,
            "battery SOC": battery_soc,
            "battery power": battery,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise UpdateFailed(f"Invalid or unavailable sensors: {', '.join(missing)}")

        phase_l1 = self._numeric_state(self._config(CONF_PHASE_L1_POWER_SENSOR), power=True)
        phase_l2 = self._numeric_state(self._config(CONF_PHASE_L2_POWER_SENSOR), power=True)
        phase_l3 = self._numeric_state(self._config(CONF_PHASE_L3_POWER_SENSOR), power=True)

        pv_potential = self._numeric_state(
            self._config(CONF_PV_POTENTIAL_POWER_SENSOR), power=True
        )
        forecast_remaining = self._numeric_state(
            self._config(CONF_PV_FORECAST_REMAINING_TODAY_SENSOR), energy=True
        )
        forecast_current_hour = self._numeric_state(
            self._config(CONF_PV_FORECAST_CURRENT_HOUR_SENSOR), energy=True
        )
        forecast_next_hour = self._numeric_state(
            self._config(CONF_PV_FORECAST_NEXT_HOUR_SENSOR), energy=True
        )
        forecast_today = self._numeric_state(
            self._config(CONF_PV_FORECAST_TODAY_SENSOR), energy=True
        )
        forecast_tomorrow = self._numeric_state(
            self._config(CONF_PV_FORECAST_TOMORROW_SENSOR), energy=True
        )
        forecast_curve = self._forecast_curve(self._config(CONF_PV_FORECAST_TODAY_SENSOR))

        extra_context_ids = self._config(CONF_EXTRA_CONTEXT_SENSORS, []) or []
        extra_context = [self._entity_snapshot(entity_id) for entity_id in extra_context_ids]
        weather_entity = self._config(CONF_WEATHER_ENTITY)
        weather_current = (
            self._entity_snapshot(str(weather_entity)) if weather_entity else None
        )

        data: dict[str, Any] = {
            "pv_power_w": pv,
            "load_power_w": load,
            "grid_power_w": grid,
            "battery_soc": battery_soc,
            "battery_power_w": battery,
            "phase_l1_power_w": phase_l1,
            "phase_l2_power_w": phase_l2,
            "phase_l3_power_w": phase_l3,
            "pv_potential_input_w": pv_potential,
            "forecast_remaining_kwh": forecast_remaining,
            "forecast_current_hour_kwh": forecast_current_hour,
            "forecast_next_hour_kwh": forecast_next_hour,
            "forecast_today_kwh": forecast_today,
            "forecast_tomorrow_kwh": forecast_tomorrow,
            "forecast_curve": forecast_curve,
            "weather_entity": weather_entity,
            "weather_current": weather_current,
            "extra_context": extra_context,
        }
        data.update(
            calculate_metrics(
                pv_power_w=pv,
                load_power_w=load,
                grid_power_w=grid,
                battery_power_w=battery,
                phase_l1_w=phase_l1,
                phase_l2_w=phase_l2,
                phase_l3_w=phase_l3,
                inverter_limit_w=float(
                    self._config(CONF_INVERTER_POWER_LIMIT, DEFAULT_INVERTER_POWER_LIMIT)
                ),
                phase_limit_w=float(
                    self._config(CONF_PHASE_POWER_LIMIT, DEFAULT_PHASE_POWER_LIMIT)
                ),
                grid_limit_w=float(
                    self._config(CONF_GRID_POWER_LIMIT, DEFAULT_GRID_POWER_LIMIT)
                ),
                safety_margin_w=float(
                    self._config(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN)
                ),
                pv_potential_power_w=pv_potential,
                battery_soc=battery_soc,
                curtailment_soc_threshold=CURTAILMENT_SOC_THRESHOLD,
                curtailment_potential_gap_w=CURTAILMENT_POTENTIAL_GAP_W,
                curtailment_grid_import_max_w=CURTAILMENT_GRID_IMPORT_MAX_W,
            )
        )
        data.update(self.ai_data)
        return data

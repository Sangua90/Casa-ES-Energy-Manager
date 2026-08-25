"""Coordinator for Casa ES Energy Manager."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .calculations import calculate_metrics
from .const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_GRID_POWER_LIMIT,
    CONF_GRID_POWER_SENSOR,
    CONF_INVERTER_POWER_LIMIT,
    CONF_LOAD_POWER_SENSOR,
    CONF_PHASE_L1_POWER_SENSOR,
    CONF_PHASE_L2_POWER_SENSOR,
    CONF_PHASE_L3_POWER_SENSOR,
    CONF_PHASE_POWER_LIMIT,
    CONF_PV_POWER_SENSOR,
    CONF_SAFETY_MARGIN,
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

    def _numeric_state(self, entity_id: str | None, *, power: bool = False) -> float | None:
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None

        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None

        if not power:
            return value

        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if unit == "kW":
            value *= 1_000.0
        elif unit == "MW":
            value *= 1_000_000.0
        return value

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

        data: dict[str, Any] = {
            "pv_power_w": pv,
            "load_power_w": load,
            "grid_power_w": grid,
            "battery_soc": battery_soc,
            "battery_power_w": battery,
            "phase_l1_power_w": phase_l1,
            "phase_l2_power_w": phase_l2,
            "phase_l3_power_w": phase_l3,
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
            )
        )
        data.update(self.ai_data)
        return data

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
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
    CONF_BATTERY_POWER_SENSOR,
    CONF_BATTERY_SOC_SENSOR,
    CONF_BATTERY_TARGET_HOUR,
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_POWER_SENSOR,
    CONF_EXPECTED_BASE_LOAD_W,
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
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
    DEFAULT_BATTERY_TARGET_HOUR,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_EXPECTED_BASE_LOAD_W,
    DEFAULT_GRID_POWER_LIMIT,
    DEFAULT_INVERTER_POWER_LIMIT,
    DEFAULT_PHASE_POWER_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    DOMAIN,
    SUBENTRY_TYPE_MANAGED_DEVICE,
    UPDATE_INTERVAL_SECONDS,
)
from .device_dry_run import evaluate_managed_devices
from .forecast_units import normalize_forecast_measure
from .planner_policy import build_planner_policy

_LOGGER = logging.getLogger(__name__)

FORECAST_CURVE_MAX_POINTS = 192


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

    def _forecast_measure(
        self, entity_id: str | None
    ) -> tuple[float | None, float | None]:
        """Read an hourly forecast as either power W or energy kWh."""
        if not entity_id:
            return None, None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None, None
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        return normalize_forecast_measure(state.state, str(unit) if unit else None)

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
        """Read a provider's optional ``watts`` timestamp-to-power curve."""
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
            for timestamp, power_w in points[:FORECAST_CURVE_MAX_POINTS]
        ]

    @staticmethod
    def _merge_forecast_curves(*curves: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge provider curves across days, deduplicating timestamps."""
        merged: dict[str, float] = {}
        for curve in curves:
            for point in curve:
                if not isinstance(point, dict):
                    continue
                raw_time = point.get("time")
                raw_power = point.get("power_w")
                if raw_time is None or raw_power is None:
                    continue
                try:
                    power_w = max(float(raw_power), 0.0)
                except (TypeError, ValueError):
                    continue
                merged[str(raw_time)] = power_w

        ordered = sorted(merged.items(), key=lambda item: item[0])
        return [
            {"time": timestamp, "power_w": round(power_w, 1)}
            for timestamp, power_w in ordered[:FORECAST_CURVE_MAX_POINTS]
        ]

    def _target_time(self) -> Any:
        """Return the configured next battery target time in local HA time."""
        now = dt_util.now()
        target_hour = int(
            self._config(CONF_BATTERY_TARGET_HOUR, DEFAULT_BATTERY_TARGET_HOUR)
        )
        target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return now, target

    def _managed_device_snapshots(self) -> list[dict[str, Any]]:
        """Read repeatable managed-device subentries without controlling them."""
        devices: list[dict[str, Any]] = []
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_MANAGED_DEVICE:
                continue
            config = dict(subentry.data)
            entity_id = str(config.get(CONF_DEVICE_ENTITY, ""))
            state = self.hass.states.get(entity_id) if entity_id else None
            power_sensor = config.get(CONF_DEVICE_POWER_SENSOR)
            current_power = self._numeric_state(
                str(power_sensor) if power_sensor else None, power=True
            )
            devices.append(
                {
                    **config,
                    "subentry_id": subentry.subentry_id,
                    "name": config.get(CONF_DEVICE_NAME) or subentry.title,
                    "entity_id": entity_id,
                    "state": state.state if state is not None else None,
                    "available": bool(
                        state is not None
                        and state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
                    ),
                    "current_power_w": current_power,
                }
            )
        return devices

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
        forecast_current_power, forecast_current_energy = self._forecast_measure(
            self._config(CONF_PV_FORECAST_CURRENT_HOUR_SENSOR)
        )
        forecast_next_power, forecast_next_energy = self._forecast_measure(
            self._config(CONF_PV_FORECAST_NEXT_HOUR_SENSOR)
        )
        forecast_today = self._numeric_state(
            self._config(CONF_PV_FORECAST_TODAY_SENSOR), energy=True
        )
        forecast_tomorrow = self._numeric_state(
            self._config(CONF_PV_FORECAST_TOMORROW_SENSOR), energy=True
        )
        forecast_curve_today = self._forecast_curve(
            self._config(CONF_PV_FORECAST_TODAY_SENSOR)
        )
        forecast_curve_tomorrow = self._forecast_curve(
            self._config(CONF_PV_FORECAST_TOMORROW_SENSOR)
        )
        forecast_curve = self._merge_forecast_curves(
            forecast_curve_today, forecast_curve_tomorrow
        )

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
            "forecast_current_hour_power_w": forecast_current_power,
            "forecast_current_hour_kwh": forecast_current_energy,
            "forecast_next_hour_power_w": forecast_next_power,
            "forecast_next_hour_kwh": forecast_next_energy,
            "forecast_today_kwh": forecast_today,
            "forecast_tomorrow_kwh": forecast_tomorrow,
            "forecast_curve_today": forecast_curve_today,
            "forecast_curve_tomorrow": forecast_curve_tomorrow,
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

        now, target = self._target_time()
        policy = build_planner_policy(
            data,
            now=now,
            target=target,
            battery_capacity_kwh=float(
                self._config(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
            ),
            battery_target_soc=float(
                self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC)
            ),
            expected_base_load_w=float(
                self._config(CONF_EXPECTED_BASE_LOAD_W, DEFAULT_EXPECTED_BASE_LOAD_W)
            ),
            battery_charge_efficiency_pct=float(
                self._config(
                    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
                    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
                )
            ),
        )
        devices = self._managed_device_snapshots()
        dry_run = evaluate_managed_devices(devices, data=data, policy=policy)

        # AI fields are advisory. Deterministic values below are always refreshed
        # after them so a 30-minute AI snapshot cannot overwrite 5-second policy.
        data.update(self.ai_data)
        data["planner_policy"] = policy
        data["managed_device_configs"] = devices
        data.update(dry_run)
        data.update(
            {
                "battery_energy_needed_kwh": policy["battery_energy_needed_kwh"],
                "battery_input_energy_needed_kwh": policy[
                    "battery_input_energy_needed_kwh"
                ],
                "base_load_energy_to_target_kwh": policy[
                    "base_load_energy_to_target_kwh"
                ],
                "forecast_energy_to_target_kwh": policy[
                    "forecast_energy_to_target_kwh"
                ],
                "forecast_margin_before_base_load_kwh": policy[
                    "forecast_margin_before_base_load_kwh"
                ],
                "forecast_margin_after_base_load_kwh": policy[
                    "forecast_margin_after_base_load_kwh"
                ],
                "flexible_energy_budget_kwh": policy["flexible_energy_budget_kwh"],
                "planner_target_reachability": policy["target_reachability"],
                "planner_grid_pressure": policy["grid_pressure"],
                "planner_solar_state": policy["solar_state"],
            }
        )
        return data

"""Casa ES Energy Manager v1.5 coordinator.

v1.5 introduces specialized load intelligence while retaining every v1.4.4
safety and persistence rule. Manual operation remains visible to the household
energy balance but is excluded from learning. DHW thermal storage uses the
native heat pump for the base temperature and Casa ES-owned Boost only for
useful photovoltaic thermal storage.
"""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .adaptive_learning_v15 import AdaptivePowerLearnerV15
from .const import (
    CONF_DEVICE_ENABLED,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_MIN_BATTERY_SOC,
    CONF_DEVICE_NOMINAL_POWER_W,
    CONF_DEVICE_TYPE,
    DEVICE_MODE_AUTO,
)
from .coordinator_v144 import CasaESEnergyCoordinator as V144Coordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_AVOID_GRID_RECOVERY,
    CONF_THERMAL_BASE_TEMP_C,
    CONF_THERMAL_BOOST_ENTITY,
    CONF_THERMAL_HARD_MAX_TEMP_C,
    CONF_THERMAL_HEATING_ENTITY,
    CONF_THERMAL_LEGIONELLA_ENTITY,
    CONF_THERMAL_LEARNING,
    CONF_THERMAL_NORMAL_MAX_TEMP_C,
    CONF_THERMAL_STRATEGY,
    DEVICE_TYPE_THERMAL,
)
from .thermal_learning_v15 import ThermalLearnerV15

_ACTIVE_STATES = {"on", "active", "true", "1", "heating", "running", "yes"}


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _state_on(state: Any) -> bool:
    if state is None:
        return False
    return str(getattr(state, "state", state)).strip().lower() in _ACTIVE_STATES


class CasaESEnergyCoordinator(V144Coordinator):
    """v1.5 controller with learning ownership and DHW thermal storage."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        # Replace the v1 learner before async_initialize() loads it.
        self.learner = AdaptivePowerLearnerV15(hass, entry.entry_id)
        self.thermal_learner = ThermalLearnerV15(hass, entry.entry_id)
        self._thermal_boost_owned: set[str] = set()
        self._thermal_target_c: dict[str, float] = {}
        self._last_thermal_action: str | None = None
        self._last_thermal_reason: str | None = None
        self._last_thermal_at: str | None = None

    async def async_initialize(self) -> None:
        await super().async_initialize()
        await self.thermal_learner.async_load()

    async def async_prepare_unload(self) -> None:
        await self.thermal_learner.async_save()
        await super().async_prepare_unload()

    def _thermal_context(self, item: dict[str, Any]) -> dict[str, Any]:
        """Attach current boiler state without changing the appliance."""
        if str(item.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_THERMAL:
            return item

        entity_id = str(item.get(CONF_DEVICE_ENTITY) or item.get("entity_id") or "")
        water = self.hass.states.get(entity_id) if entity_id else None
        attrs = water.attributes if water is not None else {}
        subentry_id = str(item.get("subentry_id") or "")

        boost_entity = str(item.get(CONF_THERMAL_BOOST_ENTITY) or "")
        heating_entity = str(item.get(CONF_THERMAL_HEATING_ENTITY) or "")
        legionella_entity = str(item.get(CONF_THERMAL_LEGIONELLA_ENTITY) or "")
        boost_state = self.hass.states.get(boost_entity) if boost_entity else None
        heating_state = self.hass.states.get(heating_entity) if heating_entity else None
        legionella_state = self.hass.states.get(legionella_entity) if legionella_entity else None

        current_temp = _f(attrs.get("current_temperature"))
        target_temp = _f(attrs.get("temperature"))
        boost_active = _state_on(boost_state)
        heating = _state_on(heating_state)
        legionella = _state_on(legionella_state)
        owned = subentry_id in self._thermal_boost_owned

        exclusion: str | None = None
        if str(item.get("management_mode") or DEVICE_MODE_AUTO) != DEVICE_MODE_AUTO:
            exclusion = "manual_mode"
        elif legionella:
            exclusion = "legionella"
        elif boost_active and not owned:
            exclusion = "manual_or_appliance_boost"

        item["thermal_current_temperature_c"] = current_temp
        item["thermal_target_temperature_c"] = target_temp
        item["thermal_boost_active"] = boost_active
        item["thermal_heating"] = heating
        item["thermal_legionella_active"] = legionella
        item["thermal_boost_owned_by_casa_es"] = owned
        item["learning_excluded"] = bool(exclusion)
        item["learning_exclusion_reason"] = exclusion
        item["thermal_profile"] = self.thermal_learner.profile(subentry_id)
        item["thermal_expected_draw_remaining_c"] = round(
            self.thermal_learner.expected_draw_c(
                subentry_id, dt_util.now().hour, 24
            ),
            2,
        )
        return item

    def _thermal_target(self, item: dict[str, Any], data: dict[str, Any], now: Any) -> tuple[float, str]:
        base = float(item.get(CONF_THERMAL_BASE_TEMP_C) or 52.0)
        normal_max = float(item.get(CONF_THERMAL_NORMAL_MAX_TEMP_C) or 65.0)
        hard_max = float(item.get(CONF_THERMAL_HARD_MAX_TEMP_C) or 72.0)
        strategy = str(item.get(CONF_THERMAL_STRATEGY) or "balanced")
        profile = item.get("thermal_profile") or {}

        # Start conservatively before enough real household behaviour is learned.
        expected_draw = min(float(item.get("thermal_expected_draw_remaining_c") or 0.0), 6.0)
        target = base + 3.0 + expected_draw
        reasons = [f"base {base:.1f}°C", f"prelievi previsti +{expected_draw:.1f}°C"]

        if strategy == "comfort":
            target += 2.0
            reasons.append("strategia comfort")
        elif strategy == "max_solar":
            target += 1.0
            reasons.append("strategia massimo FV")

        loss = max(float(profile.get("standby_loss_c_per_h") or 0.0), 0.0)
        avoid_grid = bool(item.get(CONF_THERMAL_AVOID_GRID_RECOVERY, True))
        forecast_tomorrow = _f(data.get("forecast_tomorrow_kwh"))
        forecast_today = _f(data.get("forecast_today_kwh"))
        tomorrow_low = False
        if forecast_tomorrow is not None:
            tomorrow_low = forecast_tomorrow < 6.0
            if forecast_today and forecast_today > 0:
                tomorrow_low = tomorrow_low or forecast_tomorrow < forecast_today * 0.35

        if avoid_grid and tomorrow_low and loss > 0:
            hours_to_morning = max((24 - now.hour) + 8, 1)
            hedge = min(loss * hours_to_morning, 5.0)
            target += hedge
            reasons.append(f"copertura mattino nuvoloso +{hedge:.1f}°C")

        curtailment = bool(data.get("curtailment_likely"))
        soc = float(data.get("battery_soc") or 0.0)
        forecast_remaining = float(data.get("forecast_remaining_kwh") or 0.0)
        limit = normal_max
        if curtailment or (soc >= 98.0 and forecast_remaining >= 2.0):
            limit = min(hard_max, normal_max + 3.0)
            target += 2.0
            reasons.append("FV abbondante/limitabile")

        return round(max(base, min(target, limit)), 1), "; ".join(reasons)

    async def _set_water_temperature(self, entity_id: str, temperature: float) -> None:
        if not self.hass.services.has_service("water_heater", "set_temperature"):
            raise HomeAssistantError("Servizio water_heater.set_temperature non disponibile")
        await self.hass.services.async_call(
            "water_heater",
            "set_temperature",
            {"entity_id": entity_id, "temperature": temperature},
            blocking=True,
        )

    async def _set_boost(self, entity_id: str, enabled: bool) -> None:
        if not entity_id:
            raise HomeAssistantError("Entità Boost non configurata")
        domain = entity_id.split(".", 1)[0]
        service = "turn_on" if enabled else "turn_off"
        service_domain = domain if self.hass.services.has_service(domain, service) else "homeassistant"
        if not self.hass.services.has_service(service_domain, service):
            raise HomeAssistantError(f"Servizio {service} non disponibile per {entity_id}")
        await self.hass.services.async_call(
            service_domain,
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def _stop_owned_thermal_boost(
        self, item: dict[str, Any], reason: str, now: Any
    ) -> None:
        subentry_id = str(item.get("subentry_id") or "")
        entity_id = str(item.get(CONF_DEVICE_ENTITY) or item.get("entity_id") or "")
        boost_entity = str(item.get(CONF_THERMAL_BOOST_ENTITY) or "")
        base = float(item.get(CONF_THERMAL_BASE_TEMP_C) or 52.0)
        await self._set_boost(boost_entity, False)
        await self._set_water_temperature(entity_id, base)
        self._thermal_boost_owned.discard(subentry_id)
        self._thermal_target_c.pop(subentry_id, None)
        self._last_thermal_action = "boost_off"
        self._last_thermal_reason = reason
        self._last_thermal_at = now.isoformat()

    async def _async_apply_thermal_control(
        self, data: dict[str, Any], now: Any
    ) -> bool:
        if not self.real_control_enabled:
            return False

        configs = [
            self._thermal_context(dict(item))
            for item in (data.get("managed_device_configs") or [])
            if str(item.get(CONF_DEVICE_TYPE) or "") == DEVICE_TYPE_THERMAL
        ]
        configs.sort(key=lambda item: int(item.get("priority") or 50))

        for item in configs:
            if not bool(item.get(CONF_DEVICE_ENABLED, True)):
                continue
            subentry_id = str(item.get("subentry_id") or "")
            if not subentry_id:
                continue

            # Internal Ariston legionella cycle owns the appliance completely.
            if item.get("thermal_legionella_active"):
                self._thermal_boost_owned.discard(subentry_id)
                self._thermal_target_c.pop(subentry_id, None)
                continue
            if str(item.get("management_mode") or DEVICE_MODE_AUTO) != DEVICE_MODE_AUTO:
                self._thermal_boost_owned.discard(subentry_id)
                self._thermal_target_c.pop(subentry_id, None)
                continue
            if item.get("thermal_boost_active") and not item.get(
                "thermal_boost_owned_by_casa_es"
            ):
                # User/app initiated Boost: observe household watts only, no learning/control.
                continue

            temp = _f(item.get("thermal_current_temperature_c"))
            if temp is None:
                continue
            base = float(item.get(CONF_THERMAL_BASE_TEMP_C) or 52.0)
            nominal = max(float(item.get(CONF_DEVICE_NOMINAL_POWER_W) or 0.0), 1.0)
            min_soc = float(item.get(CONF_DEVICE_MIN_BATTERY_SOC) or 0.0)
            soc = float(data.get("battery_soc") or 0.0)
            measured_surplus = max(float(data.get("solar_after_house_w") or 0.0), 0.0)
            potential_surplus = max(float(data.get("pv_potential_after_house_w") or 0.0), 0.0)
            surplus = max(measured_surplus, potential_surplus)
            curtailment = bool(data.get("curtailment_likely"))

            owned = subentry_id in self._thermal_boost_owned
            if owned:
                target = float(self._thermal_target_c.get(subentry_id, base))
                if temp >= target - 0.3:
                    await self._stop_owned_thermal_boost(
                        item, f"Target termico {target:.1f}°C raggiunto", now
                    )
                    return True
                if soc < min_soc:
                    await self._stop_owned_thermal_boost(
                        item, f"SOC {soc:.0f}% sotto minimo {min_soc:.0f}%", now
                    )
                    return True
                if surplus < nominal * 0.35 and not curtailment:
                    await self._stop_owned_thermal_boost(
                        item, "Surplus FV non più sufficiente", now
                    )
                    return True
                continue

            # The native heat pump owns everything below the base temperature.
            if temp < base - 0.5:
                continue
            if soc < min_soc:
                continue
            if surplus < nominal * 0.9 and not curtailment:
                continue

            target, reason = self._thermal_target(item, data, now)
            if target <= temp + 0.5:
                continue
            boost_entity = str(item.get(CONF_THERMAL_BOOST_ENTITY) or "")
            entity_id = str(item.get(CONF_DEVICE_ENTITY) or item.get("entity_id") or "")
            if not boost_entity or not entity_id:
                continue

            await self._set_water_temperature(entity_id, target)
            await self._set_boost(boost_entity, True)
            self._thermal_boost_owned.add(subentry_id)
            self._thermal_target_c[subentry_id] = target
            self._last_thermal_action = "boost_on"
            self._last_thermal_reason = f"Target {target:.1f}°C: {reason}"
            self._last_thermal_at = now.isoformat()
            return True

        return False

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        """Keep thermal storage out of generic on/off control, then run its manager."""
        original = list(data.get("dry_run_decisions") or [])
        thermal_ids = {
            str(item.get("subentry_id") or "")
            for item in (data.get("managed_device_configs") or [])
            if str(item.get(CONF_DEVICE_TYPE) or "") == DEVICE_TYPE_THERMAL
        }
        data["dry_run_decisions"] = [
            item for item in original if str(item.get("subentry_id") or "") not in thermal_ids
        ]
        before = self._last_real_control_at
        try:
            await super()._async_apply_real_control(data, now)
        finally:
            data["dry_run_decisions"] = original

        # Preserve one appliance-management action per refresh whenever possible.
        generic_command_sent = self._last_real_control_at != before
        if not generic_command_sent:
            try:
                thermal_sent = await self._async_apply_thermal_control(data, now)
            except Exception as err:
                self._last_thermal_action = "error"
                self._last_thermal_reason = str(err)
                self._last_thermal_at = now.isoformat()
                thermal_sent = False
            if thermal_sent:
                data["real_control_status"] = "thermal_command_sent"

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        devices = [
            self._thermal_context(dict(item))
            for item in (data.get("managed_device_configs") or [])
        ]
        data["managed_device_configs"] = devices

        learning_devices = [
            item
            for item in devices
            if item.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_THERMAL
            and bool(item.get(CONF_THERMAL_LEARNING, True))
        ]
        await self.thermal_learner.async_observe(learning_devices)
        # Reattach profiles immediately after a new observation.
        for item in devices:
            if item.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_THERMAL:
                item["thermal_profile"] = self.thermal_learner.profile(
                    str(item.get("subentry_id") or "")
                )

        data["thermal_profiles"] = self.thermal_learner.export()
        data["thermal_last_action"] = self._last_thermal_action
        data["thermal_last_reason"] = self._last_thermal_reason
        data["thermal_last_at"] = self._last_thermal_at
        data["v15_learning_manual_exclusion"] = True
        data["v15_thermal_storage_enabled"] = True
        return data

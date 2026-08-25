"""v1 coordinator extensions for Casa ES Energy Manager."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .adaptive_learning import AdaptivePowerLearner
from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_ADAPTIVE_POWER,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_NOMINAL_POWER_W,
    CONF_EMERGENCY_CHARGE_MAX_MINUTES,
    CONF_EMERGENCY_CHARGE_POWER_W,
    CONF_EMERGENCY_CHARGE_START_SCRIPT,
    CONF_EMERGENCY_CHARGE_STOP_SCRIPT,
    CONF_EMERGENCY_CHARGE_TARGET_SOC,
    CONF_ENERGY_PREFERENCE,
    CONF_EXPECTED_BASE_LOAD_W,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CHARGE_EFFICIENCY_PCT,
    DEFAULT_BATTERY_TARGET_SOC,
    DEFAULT_DEVICE_ADAPTIVE_POWER,
    DEFAULT_EMERGENCY_CHARGE_MAX_MINUTES,
    DEFAULT_EMERGENCY_CHARGE_POWER_W,
    DEFAULT_EMERGENCY_CHARGE_TARGET_SOC,
    DEFAULT_ENERGY_PREFERENCE,
    DEFAULT_EXPECTED_BASE_LOAD_W,
    DEVICE_MODE_AUTO,
    LEGACY_REMOVED_DEVICE_KEYS,
)
from .coordinator import CasaESEnergyCoordinator as BaseCoordinator
from .device_dry_run_v1 import evaluate_managed_devices
from .phase_attribution import phase_attribution
from .planner_policy_v1 import build_planner_policy


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CasaESEnergyCoordinator(BaseCoordinator):
    """Coordinator with runtime modes, learning and manual charge hooks."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self.device_modes: dict[str, str] = {}
        self.learner = AdaptivePowerLearner(hass, entry.entry_id)
        self.emergency_charge_active = False
        self.emergency_charge_deadline = None
        self.emergency_charge_started_at = None
        self.emergency_charge_stop_reason: str | None = None

    async def async_initialize(self) -> None:
        await self.learner.async_load()

    async def async_prepare_unload(self) -> None:
        if self.emergency_charge_active:
            await self.async_stop_emergency_charge("integration_unload", refresh=False)
        await self.learner.async_save()

    def set_device_mode(self, subentry_id: str, mode: str) -> None:
        self.device_modes[subentry_id] = mode

    def _script_configured(self, key: str) -> bool:
        value = self._config(key)
        return bool(value and str(value).startswith("script."))

    @property
    def emergency_charge_available(self) -> bool:
        return self._script_configured(
            CONF_EMERGENCY_CHARGE_START_SCRIPT
        ) and self._script_configured(CONF_EMERGENCY_CHARGE_STOP_SCRIPT)

    def _validate_emergency_charge_headroom(self, power_w: float) -> None:
        """Reject a requested manual charge that does not fit measured limits."""
        data = self.data or {}
        grid_headroom = _float(data.get("grid_headroom_w"))
        inverter_headroom = _float(data.get("inverter_headroom_w"))
        if grid_headroom is not None and power_w > grid_headroom + 1e-9:
            raise HomeAssistantError(
                f"Ricarica emergenza non avviata: servono {power_w:.0f} W ma il margine rete è {grid_headroom:.0f} W."
            )
        if inverter_headroom is not None and power_w > inverter_headroom + 1e-9:
            raise HomeAssistantError(
                f"Ricarica emergenza non avviata: margine inverter insufficiente ({inverter_headroom:.0f} W)."
            )
        phase_need = power_w / 3.0
        known_phase_headrooms = [
            value
            for value in (
                _float(data.get("phase_l1_headroom_w")),
                _float(data.get("phase_l2_headroom_w")),
                _float(data.get("phase_l3_headroom_w")),
            )
            if value is not None
        ]
        if known_phase_headrooms and min(known_phase_headrooms) < phase_need - 1e-9:
            raise HomeAssistantError(
                "Ricarica emergenza non avviata: il margine di almeno una fase è insufficiente per la potenza richiesta."
            )
        policy = data.get("planner_policy") or {}
        if policy.get("protect_grid_required"):
            raise HomeAssistantError(
                "Ricarica emergenza non avviata: è attiva una condizione di protezione elettrica locale."
            )

    async def async_start_emergency_charge(self) -> None:
        """Invoke the user-configured inverter-specific start script."""
        if not self.emergency_charge_available:
            raise HomeAssistantError(
                "Configura gli script di avvio e arresto ricarica di emergenza nelle opzioni Casa ES."
            )
        if self.emergency_charge_active:
            return
        target_soc = float(
            self._config(
                CONF_EMERGENCY_CHARGE_TARGET_SOC,
                DEFAULT_EMERGENCY_CHARGE_TARGET_SOC,
            )
        )
        power_w = float(
            self._config(CONF_EMERGENCY_CHARGE_POWER_W, DEFAULT_EMERGENCY_CHARGE_POWER_W)
        )
        max_minutes = int(
            self._config(
                CONF_EMERGENCY_CHARGE_MAX_MINUTES,
                DEFAULT_EMERGENCY_CHARGE_MAX_MINUTES,
            )
        )
        current_soc = _float((self.data or {}).get("battery_soc"))
        if current_soc is not None and current_soc >= target_soc:
            raise HomeAssistantError(
                "La batteria ha già raggiunto il SOC obiettivo della ricarica di emergenza."
            )
        self._validate_emergency_charge_headroom(power_w)
        script = str(self._config(CONF_EMERGENCY_CHARGE_START_SCRIPT))
        await self.hass.services.async_call(
            "script",
            "turn_on",
            {
                "entity_id": script,
                "variables": {
                    "power_w": power_w,
                    "target_soc": target_soc,
                    "max_minutes": max_minutes,
                },
            },
            blocking=True,
        )
        now = dt_util.now()
        self.emergency_charge_active = True
        self.emergency_charge_started_at = now
        self.emergency_charge_deadline = now + timedelta(minutes=max_minutes)
        self.emergency_charge_stop_reason = None
        await self.async_request_refresh()

    async def async_stop_emergency_charge(
        self, reason: str = "manual", *, refresh: bool = True
    ) -> None:
        """Invoke the user-configured inverter-specific stop script."""
        script = self._config(CONF_EMERGENCY_CHARGE_STOP_SCRIPT)
        if script and str(script).startswith("script."):
            await self.hass.services.async_call(
                "script",
                "turn_on",
                {"entity_id": str(script)},
                blocking=True,
            )
        self.emergency_charge_active = False
        self.emergency_charge_deadline = None
        self.emergency_charge_stop_reason = reason
        if refresh:
            await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
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
            energy_preference=str(
                self._config(CONF_ENERGY_PREFERENCE, DEFAULT_ENERGY_PREFERENCE)
            ),
        )

        devices = [dict(item) for item in (data.get("managed_device_configs") or [])]
        for item in devices:
            # v1.1 deliberately ignores the old dependency/wallbox/EV model even
            # when those keys still exist in an older stored subentry.
            for key in LEGACY_REMOVED_DEVICE_KEYS:
                item.pop(key, None)

            subentry_id = str(item.get("subentry_id") or "")
            item["management_mode"] = self.device_modes.get(
                subentry_id, DEVICE_MODE_AUTO
            )
            entity_id = str(item.get(CONF_DEVICE_ENTITY) or item.get("entity_id") or "")
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is not None and entity_id.startswith("climate."):
                item["hvac_mode"] = state.state
                item["hvac_action"] = state.attributes.get("hvac_action")
            else:
                item["hvac_mode"] = None
                item["hvac_action"] = None

        await self.learner.async_observe(devices)

        for item in devices:
            entity_id = str(item.get("entity_id") or "")
            nominal = float(item.get(CONF_DEVICE_NOMINAL_POWER_W) or 0.0)
            adaptive = bool(
                item.get(CONF_DEVICE_ADAPTIVE_POWER, DEFAULT_DEVICE_ADAPTIVE_POWER)
            )
            if adaptive and item.get("power_sensor"):
                mode = self.learner.mode_for(item)
                profile = self.learner.profile_for(entity_id, mode, nominal)
                item["adaptive_profile"] = profile
                item["admission_power_w"] = profile["estimated_power_w"]
            else:
                item["adaptive_profile"] = {
                    "status": "not_applicable",
                    "samples": 0,
                }
                item["admission_power_w"] = nominal

        data["planner_policy"] = policy
        data["managed_device_configs"] = devices
        data.update(
            evaluate_managed_devices(devices, data=data, policy=policy, now=now)
        )
        data.update(
            phase_attribution(
                data.get("monitored_loads") or [],
                devices,
                phase_l1_w=data.get("phase_l1_power_w"),
                phase_l2_w=data.get("phase_l2_power_w"),
                phase_l3_w=data.get("phase_l3_power_w"),
            )
        )
        data["adaptive_power_profiles"] = self.learner.export()
        data["energy_preference"] = policy["energy_preference"]

        for key in (
            "battery_energy_needed_kwh",
            "battery_input_energy_needed_kwh",
            "base_load_energy_to_target_kwh",
            "forecast_energy_to_target_kwh",
            "forecast_margin_before_base_load_kwh",
            "forecast_margin_after_base_load_kwh",
            "flexible_energy_budget_kwh",
        ):
            data[key] = policy.get(key)
        data["planner_target_reachability"] = policy.get("target_reachability")
        data["planner_grid_pressure"] = policy.get("grid_pressure")
        data["planner_solar_state"] = policy.get("solar_state")

        if self.emergency_charge_active:
            target_soc = float(
                self._config(
                    CONF_EMERGENCY_CHARGE_TARGET_SOC,
                    DEFAULT_EMERGENCY_CHARGE_TARGET_SOC,
                )
            )
            soc = float(data.get("battery_soc") or 0.0)
            if policy.get("protect_grid_required"):
                await self.async_stop_emergency_charge(
                    "electrical_protection", refresh=False
                )
            elif soc >= target_soc:
                await self.async_stop_emergency_charge(
                    "target_soc_reached", refresh=False
                )
            elif self.emergency_charge_deadline and now >= self.emergency_charge_deadline:
                await self.async_stop_emergency_charge("timeout", refresh=False)

        data["emergency_charge_active"] = self.emergency_charge_active
        data["emergency_charge_available"] = self.emergency_charge_available
        data["emergency_charge_target_soc"] = float(
            self._config(
                CONF_EMERGENCY_CHARGE_TARGET_SOC,
                DEFAULT_EMERGENCY_CHARGE_TARGET_SOC,
            )
        )
        data["emergency_charge_power_w"] = float(
            self._config(CONF_EMERGENCY_CHARGE_POWER_W, DEFAULT_EMERGENCY_CHARGE_POWER_W)
        )
        data["emergency_charge_deadline"] = (
            self.emergency_charge_deadline.isoformat()
            if self.emergency_charge_deadline is not None
            else None
        )
        data["emergency_charge_stop_reason"] = self.emergency_charge_stop_reason
        return data

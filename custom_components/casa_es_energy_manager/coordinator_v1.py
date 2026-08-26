"""v1 coordinator extensions for Casa ES Energy Manager."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .adaptive_learning import AdaptivePowerLearner
from .const import (
    CONF_AUTOMATIC_REAL_LOAD_CONTROL,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CHARGE_EFFICIENCY_PCT,
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_ADAPTIVE_POWER,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES,
    CONF_DEVICE_MODE_CLIMATE_ENTITY,
    CONF_DEVICE_NOMINAL_POWER_W,
    CONF_DEVICE_TYPE,
    CONF_EMERGENCY_CHARGE_MAX_MINUTES,
    CONF_EMERGENCY_CHARGE_POWER_W,
    CONF_EMERGENCY_CHARGE_START_SCRIPT,
    CONF_EMERGENCY_CHARGE_STOP_SCRIPT,
    CONF_EMERGENCY_CHARGE_TARGET_SOC,
    CONF_ENERGY_PREFERENCE,
    CONF_EXPECTED_BASE_LOAD_W,
    DEFAULT_AUTOMATIC_REAL_LOAD_CONTROL,
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
    DEVICE_MODE_OVERRIDE,
    DEVICE_TYPE_CLIMATE,
    LEGACY_REMOVED_DEVICE_KEYS,
    REAL_CONTROL_COMMAND_COOLDOWN_SECONDS,
    UPDATE_INTERVAL_SECONDS,
)
from .coordinator import CasaESEnergyCoordinator as BaseCoordinator
from .device_dry_run import _is_running, _state_active
from .device_dry_run_v1 import evaluate_managed_devices
from .phase_attribution import phase_attribution
from .planner_policy_v1 import build_planner_policy


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any, default: float = 0.0) -> float:
    converted = _float(value)
    return default if converted is None else converted


class CasaESEnergyCoordinator(BaseCoordinator):
    """Coordinator with runtime modes, learning and guarded real control."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self.device_modes: dict[str, str] = {}
        self.learner = AdaptivePowerLearner(hass, entry.entry_id)
        self.emergency_charge_active = False
        self.emergency_charge_deadline = None
        self.emergency_charge_started_at = None
        self.emergency_charge_stop_reason: str | None = None

        self._runtime_day = dt_util.now().date()
        self._runtime_last_update = None
        self._runtime_seconds: dict[str, float] = {}
        self._runtime_activations: dict[str, int] = {}
        self._runtime_previous: dict[str, bool] = {}

        self._last_command_at: dict[str, Any] = {}
        self._last_real_control_action: str | None = None
        self._last_real_control_entity: str | None = None
        self._last_real_control_reason: str | None = None
        self._last_real_control_at: str | None = None
        self._last_real_control_error: str | None = None

    async def async_initialize(self) -> None:
        await self.learner.async_load()

    async def async_prepare_unload(self) -> None:
        if self.emergency_charge_active:
            await self.async_stop_emergency_charge("integration_unload", refresh=False)
        await self.learner.async_save()

    def set_device_mode(self, subentry_id: str, mode: str) -> None:
        self.device_modes[subentry_id] = mode

    @property
    def real_control_enabled(self) -> bool:
        return bool(
            self._config(
                CONF_AUTOMATIC_REAL_LOAD_CONTROL,
                DEFAULT_AUTOMATIC_REAL_LOAD_CONTROL,
            )
        )

    def _script_configured(self, key: str) -> bool:
        value = self._config(key)
        return bool(value and str(value).startswith("script."))

    @property
    def emergency_charge_available(self) -> bool:
        return self._script_configured(
            CONF_EMERGENCY_CHARGE_START_SCRIPT
        ) and self._script_configured(CONF_EMERGENCY_CHARGE_STOP_SCRIPT)

    def _validate_emergency_charge_headroom(self, power_w: float) -> None:
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

    def _attach_mode_context(self, item: dict[str, Any]) -> None:
        """Attach managed state plus an optional climate/PDC profile mode."""
        entity_id = str(item.get(CONF_DEVICE_ENTITY) or item.get("entity_id") or "")
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is not None and entity_id.startswith("climate."):
            item["hvac_mode"] = state.state
            item["hvac_action"] = state.attributes.get("hvac_action")
        else:
            item["hvac_mode"] = None
            item["hvac_action"] = None

        configured_type = str(item.get(CONF_DEVICE_TYPE) or "")
        is_climate = configured_type == DEVICE_TYPE_CLIMATE or entity_id.startswith(
            "climate."
        )
        item[CONF_DEVICE_TYPE] = DEVICE_TYPE_CLIMATE if is_climate else configured_type or "generic"
        item["profile_mode"] = None
        item["profile_hvac_action"] = None
        item["mode_reference_entity"] = None
        item["mode_reference_available"] = None
        item["mode_reference_required"] = False

        if not is_climate:
            return

        reference = str(item.get(CONF_DEVICE_MODE_CLIMATE_ENTITY) or "")
        if not reference and entity_id.startswith("climate."):
            reference = entity_id
        if not reference:
            item["mode_reference_required"] = True
            item["mode_reference_available"] = False
            return

        reference_state = self.hass.states.get(reference)
        available = bool(
            reference_state is not None
            and reference_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        )
        item["mode_reference_entity"] = reference
        item["mode_reference_required"] = True
        item["mode_reference_available"] = available
        if available and reference_state is not None:
            item["profile_mode"] = reference_state.state
            item["profile_hvac_action"] = reference_state.attributes.get("hvac_action")

    def _apply_runtime_tracking(self, devices: list[dict[str, Any]], now: Any) -> None:
        """Track today's real active minutes and activations in memory."""
        today = now.date()
        if today != self._runtime_day:
            self._runtime_day = today
            self._runtime_seconds.clear()
            self._runtime_activations.clear()
            self._runtime_previous.clear()
            self._runtime_last_update = None

        elapsed = 0.0
        if self._runtime_last_update is not None:
            elapsed = max((now - self._runtime_last_update).total_seconds(), 0.0)
            elapsed = min(elapsed, UPDATE_INTERVAL_SECONDS * 3.0)

        for item in devices:
            subentry_id = str(item.get("subentry_id") or "")
            shared = bool(item.get("adaptive_shared_power_sensor"))
            running = _is_running(
                item.get("state"),
                item.get("current_power_w"),
                shared_power_sensor=shared,
            )
            entity_active = _state_active(item.get("state"))
            item["running"] = running
            item["entity_active"] = entity_active

            if subentry_id:
                if running and elapsed > 0:
                    self._runtime_seconds[subentry_id] = (
                        self._runtime_seconds.get(subentry_id, 0.0) + elapsed
                    )
                previous = self._runtime_previous.get(subentry_id)
                if previous is not None and running and not previous:
                    self._runtime_activations[subentry_id] = (
                        self._runtime_activations.get(subentry_id, 0) + 1
                    )
                self._runtime_previous[subentry_id] = running

            runtime_minutes = self._runtime_seconds.get(subentry_id, 0.0) / 60.0
            activations = self._runtime_activations.get(subentry_id, 0)
            min_daily = max(
                _number(item.get(CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES)), 0.0
            )
            item["daily_runtime_minutes"] = round(runtime_minutes, 2)
            item["daily_activations"] = activations
            item["remaining_min_daily_runtime_minutes"] = round(
                max(min_daily - runtime_minutes, 0.0), 2
            )

        self._runtime_last_update = now

    def _command_recent(self, entity_id: str, now: Any) -> bool:
        previous = self._last_command_at.get(entity_id)
        if previous is None:
            return False
        return (now - previous).total_seconds() < REAL_CONTROL_COMMAND_COOLDOWN_SECONDS

    async def _async_call_entity_control(self, entity_id: str, turn_on: bool) -> None:
        domain = entity_id.split(".", 1)[0]
        service = "turn_on" if turn_on else "turn_off"
        if self.hass.services.has_service(domain, service):
            service_domain = domain
        elif self.hass.services.has_service("homeassistant", service):
            service_domain = "homeassistant"
        else:
            raise HomeAssistantError(
                f"Nessun servizio {service} disponibile per {entity_id}."
            )
        await self.hass.services.async_call(
            service_domain,
            service,
            {"entity_id": entity_id},
            blocking=True,
        )

    def _write_real_control_diagnostics(self, data: dict[str, Any], status: str) -> None:
        data["automatic_real_load_control"] = self.real_control_enabled
        data["real_control_status"] = status
        data["last_real_control_action"] = self._last_real_control_action
        data["last_real_control_entity"] = self._last_real_control_entity
        data["last_real_control_reason"] = self._last_real_control_reason
        data["last_real_control_at"] = self._last_real_control_at
        data["last_real_control_error"] = self._last_real_control_error

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        """Execute at most one deterministic appliance command per refresh."""
        if not self.real_control_enabled:
            self._write_real_control_diagnostics(data, "disabled")
            return

        decisions = list(data.get("dry_run_decisions") or [])
        stop_candidates = [
            item
            for item in decisions
            if item.get("would_stop")
            and item.get("management_mode") != DEVICE_MODE_OVERRIDE
            and item.get("entity_active")
        ]
        stop_candidates.sort(
            key=lambda item: (
                0 if item.get("stop_is_hard_safety") else 1,
                -int(item.get("priority") or 50),
            )
        )
        start_candidates = [
            item
            for item in decisions
            if item.get("would_start")
            and item.get("management_mode") == DEVICE_MODE_AUTO
        ]
        start_candidates.sort(key=lambda item: int(item.get("priority") or 50))

        selected: tuple[dict[str, Any], bool] | None = None
        for item in stop_candidates:
            entity_id = str(item.get("entity_id") or "")
            if entity_id and not self._command_recent(entity_id, now):
                selected = (item, False)
                break
        if selected is None:
            for item in start_candidates:
                entity_id = str(item.get("entity_id") or "")
                if entity_id and not self._command_recent(entity_id, now):
                    selected = (item, True)
                    break

        if selected is None:
            self._write_real_control_diagnostics(data, "enabled")
            return

        decision, turn_on = selected
        entity_id = str(decision.get("entity_id") or "")
        action = "turn_on" if turn_on else "turn_off"
        reason = str(decision.get("reason") or "Decisione deterministica Casa ES")
        try:
            await self._async_call_entity_control(entity_id, turn_on)
        except Exception as err:  # Home Assistant service errors must be diagnostic, not fatal.
            self._last_command_at[entity_id] = now
            self._last_real_control_action = action
            self._last_real_control_entity = entity_id
            self._last_real_control_reason = reason
            self._last_real_control_at = now.isoformat()
            self._last_real_control_error = str(err)
            self._write_real_control_diagnostics(data, "error")
            return

        self._last_command_at[entity_id] = now
        self._last_real_control_action = action
        self._last_real_control_entity = entity_id
        self._last_real_control_reason = reason
        self._last_real_control_at = now.isoformat()
        self._last_real_control_error = None
        self._write_real_control_diagnostics(data, "command_sent")

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
            for key in LEGACY_REMOVED_DEVICE_KEYS:
                item.pop(key, None)

            subentry_id = str(item.get("subentry_id") or "")
            item["management_mode"] = self.device_modes.get(
                subentry_id, DEVICE_MODE_AUTO
            )
            self._attach_mode_context(item)

        power_sensor_counts = Counter(
            str(item.get("power_sensor") or "")
            for item in devices
            if item.get("power_sensor")
        )
        for item in devices:
            power_sensor = str(item.get("power_sensor") or "")
            item["adaptive_shared_power_sensor"] = bool(
                power_sensor and power_sensor_counts[power_sensor] > 1
            )

        self._apply_runtime_tracking(devices, now)
        await self.learner.async_observe(devices)

        for item in devices:
            nominal = float(item.get(CONF_DEVICE_NOMINAL_POWER_W) or 0.0)
            adaptive = bool(
                item.get(CONF_DEVICE_ADAPTIVE_POWER, DEFAULT_DEVICE_ADAPTIVE_POWER)
            )
            if adaptive and item.get("power_sensor"):
                profile = self.learner.admission_profile_for(item, nominal)
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

        await self._async_apply_real_control(data, now)
        return data

"""Casa ES Energy Manager v1.5.4 cycle accounting and surplus-harvest fixes.

v1.5.4 counts daily activations from real managed-entity OFF->ON transitions,
not from compressor/power-sensor modulation. It also allows near-target solar
curtailment to feed eligible flexible loads instead of wasting available PV.
"""

from __future__ import annotations

import math
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_TARGET_SOC,
    CONF_DEVICE_MIN_DAILY_RUNTIME_MINUTES,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_TYPE,
    DEFAULT_BATTERY_TARGET_SOC,
    DEVICE_MODE_AUTO,
    DEVICE_TYPE_CLIMATE,
    UPDATE_INTERVAL_SECONDS,
)
from .coordinator_v153 import CasaESEnergyCoordinator as V153Coordinator
from .device_dry_run import _is_running, _state_active

ACTIVATION_COUNTER_MODE = "entity_off_to_on_v154"
CURTAILMENT_TARGET_SOC_WINDOW_PCT = 2.5
CURTAILMENT_HARVEST_RESERVE_W = 150.0
CURTAILMENT_NEAR_TARGET_PROBE_MAX_W = 300.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V153Coordinator):
    """v1.5.4 coordinator with real-cycle accounting and PV harvest."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._v154_activation_counter_migrated = False
        self._v154_activation_store_marked = False

    async def async_initialize(self) -> None:
        await super().async_initialize()
        stored = await self._runtime_store.async_load()
        if isinstance(stored, dict):
            self._v154_activation_store_marked = (
                stored.get("activation_counter_mode") == ACTIVATION_COUNTER_MODE
            )

    async def _async_save_runtime_state(self) -> None:
        await super()._async_save_runtime_state()
        stored = await self._runtime_store.async_load()
        if not isinstance(stored, dict):
            return
        if stored.get("activation_counter_mode") == ACTIVATION_COUNTER_MODE:
            self._v154_activation_store_marked = True
            return
        stored["activation_counter_mode"] = ACTIVATION_COUNTER_MODE
        await self._runtime_store.async_save(stored)
        self._v154_activation_store_marked = True

    def _repair_legacy_activation_counts(self, devices: list[dict[str, Any]]) -> None:
        """Clamp legacy power-edge counts once when moving to entity-edge counting."""
        if self._v154_activation_counter_migrated:
            return

        for item in devices:
            subentry_id = str(item.get("subentry_id") or "")
            if not subentry_id:
                continue
            entity_active = _state_active(item.get("state"))

            if not self._v154_activation_store_marked:
                configured_type = str(item.get(CONF_DEVICE_TYPE) or "")
                if configured_type == DEVICE_TYPE_CLIMATE:
                    runtime_min = max(self._runtime_seconds.get(subentry_id, 0.0) / 60.0, 0.0)
                    min_on = max(_number(item.get(CONF_DEVICE_MIN_ON_MINUTES), 0.0), 0.0)
                    old_count = max(int(self._runtime_activations.get(subentry_id, 0)), 0)
                    if min_on > 0 and old_count > 0:
                        # A true automatic climate cycle cannot legitimately be
                        # shorter than min_on. Use runtime only to remove clearly
                        # impossible legacy power-modulation edges.
                        plausible_max = max(int(math.ceil(runtime_min / min_on)), 1)
                        self._runtime_activations[subentry_id] = min(old_count, plausible_max)

            # From this point onward previous means the managed entity's actual
            # active state, never power-sensor/compressor activity.
            self._runtime_previous[subentry_id] = entity_active

        self._v154_activation_counter_migrated = True

    def _apply_runtime_tracking(self, devices: list[dict[str, Any]], now: Any) -> None:
        """Track runtime by power, but activations only by real entity OFF->ON."""
        today = now.date()
        if today != self._runtime_day:
            self._runtime_day = today
            self._runtime_seconds.clear()
            self._runtime_activations.clear()
            self._runtime_previous.clear()
            self._runtime_last_update = None
            self._v154_activation_counter_migrated = True
            self._v154_activation_store_marked = True

        self._repair_legacy_activation_counts(devices)

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
                previous_active = self._runtime_previous.get(subentry_id)
                if previous_active is not None and entity_active and not previous_active:
                    self._runtime_activations[subentry_id] = (
                        self._runtime_activations.get(subentry_id, 0) + 1
                    )
                self._runtime_previous[subentry_id] = entity_active

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
            item["activation_counter_source"] = ACTIVATION_COUNTER_MODE

        self._runtime_last_update = now
        self._runtime_dirty_refreshes += 1

    def _curtailment_harvest_available(self, data: dict[str, Any]) -> bool:
        target_soc = _number(
            self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC),
            DEFAULT_BATTERY_TARGET_SOC,
        )
        soc = _number(data.get("battery_soc"), 0.0)
        return bool(
            data.get("pv_curtailment_likely")
            and not data.get("grid_warning")
            and not data.get("inverter_warning")
            and _number(data.get("grid_import_w"), 0.0) <= 100.0
            and soc >= target_soc - CURTAILMENT_TARGET_SOC_WINDOW_PCT
        )

    def _apply_curtailment_harvest(self, data: dict[str, Any]) -> None:
        """Release battery-first blocking when clipped PV can cover the load."""
        if not self._curtailment_harvest_available(data):
            data["v154_curtailment_harvest_active"] = False
            return

        target_soc = _number(
            self._config(CONF_BATTERY_TARGET_SOC, DEFAULT_BATTERY_TARGET_SOC),
            DEFAULT_BATTERY_TARGET_SOC,
        )
        soc = _number(data.get("battery_soc"), 0.0)
        target_reached = soc >= target_soc - 0.1
        available_w = max(_number(data.get("pv_potential_after_house_w"), 0.0), 0.0)
        measured_surplus_w = max(_number(data.get("solar_after_house_w"), 0.0), 0.0)
        phase_headroom = {
            "l1": max(_number(data.get("phase_l1_headroom_w"), 0.0), 0.0),
            "l2": max(_number(data.get("phase_l2_headroom_w"), 0.0), 0.0),
            "l3": max(_number(data.get("phase_l3_headroom_w"), 0.0), 0.0),
        }
        released: list[str] = []

        for decision in data.get("dry_run_decisions") or []:
            if not isinstance(decision, dict):
                continue
            if decision.get("management_mode") != DEVICE_MODE_AUTO:
                continue

            reason = str(decision.get("reason") or "")
            battery_block = (
                decision.get("decision") == "waiting_energy"
                or "Margine batteria stretto" in reason
            )
            if not battery_block:
                continue

            power_w = max(
                _number(decision.get("admission_power_w"), decision.get("nominal_power_w", 0.0)),
                0.0,
            )
            if power_w <= 0 or power_w + CURTAILMENT_HARVEST_RESERVE_W > available_w:
                continue

            # Below the actual target, use potential/curtailment as a cautious
            # probe only for small loads (e.g. dehumidifier). Larger loads are
            # released only if measured surplus already covers them. Once the
            # battery target is reached, all clipped PV can be harvested.
            if (
                not target_reached
                and power_w > CURTAILMENT_NEAR_TARGET_PROBE_MAX_W
                and power_w + CURTAILMENT_HARVEST_RESERVE_W > measured_surplus_w
            ):
                continue

            phase = str(decision.get("phase") or "")
            if phase in phase_headroom and power_w + CURTAILMENT_HARVEST_RESERVE_W > phase_headroom[phase]:
                continue

            if decision.get("entity_active"):
                if decision.get("would_stop") and not decision.get("stop_is_hard_safety"):
                    decision["would_stop"] = False
                    decision["decision"] = "curtailment_harvest_running"
                    decision["reason"] = (
                        "FV potenziale inutilizzato disponibile: mantengo il carico attivo "
                        "senza sacrificare il target batteria."
                    )
                    released.append(str(decision.get("name") or decision.get("entity_id") or ""))
                continue

            decision["would_start"] = True
            decision["decision"] = "curtailment_harvest_start"
            decision["reason"] = (
                "FV potenziale inutilizzato disponibile: avvio il carico per assorbire "
                "surplus che altrimenti verrebbe limitato."
            )
            released.append(str(decision.get("name") or decision.get("entity_id") or ""))

        data["v154_curtailment_harvest_active"] = bool(released)
        data["v154_curtailment_harvest_candidates"] = released
        data["v154_curtailment_harvest_available_w"] = round(available_w, 1)
        data["v154_curtailment_measured_surplus_w"] = round(measured_surplus_w, 1)
        data["v154_curtailment_target_reached"] = target_reached

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        self._apply_curtailment_harvest(data)
        await super()._async_apply_real_control(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v154_activation_counter_mode"] = ACTIVATION_COUNTER_MODE
        data["v154_activation_counter_migrated"] = self._v154_activation_counter_migrated
        data["v154_min_on_normal_stop_enforced"] = True
        data["v154_curtailment_target_soc_window_pct"] = CURTAILMENT_TARGET_SOC_WINDOW_PCT
        data["v154_curtailment_harvest_reserve_w"] = CURTAILMENT_HARVEST_RESERVE_W
        data["v154_curtailment_near_target_probe_max_w"] = CURTAILMENT_NEAR_TARGET_PROBE_MAX_W
        return data

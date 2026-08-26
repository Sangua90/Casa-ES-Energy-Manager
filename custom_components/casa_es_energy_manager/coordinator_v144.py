"""v1.4.4 coordinator: persistent runtime accounting and PV-first daily minima."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .coordinator_v143 import CasaESEnergyCoordinator as V143Coordinator
from .daily_minimum_policy import should_defer_daily_minimum_start

RUNTIME_STORAGE_VERSION = 1
RUNTIME_SCHEMA_VERSION = 1
RUNTIME_SAVE_EVERY_REFRESHES = 12


class CasaESEnergyCoordinator(V143Coordinator):
    """v1.4.4 controller with restart-safe daily runtime state."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._runtime_store: Store[dict[str, Any]] = Store(
            hass,
            RUNTIME_STORAGE_VERSION,
            f"casa_es_energy_manager.{entry.entry_id}.runtime_state",
        )
        self._runtime_dirty_refreshes = 0
        self._runtime_last_saved_at: str | None = None
        self._runtime_restored = False

    async def async_initialize(self) -> None:
        await super().async_initialize()
        stored = await self._runtime_store.async_load()
        today = dt_util.now().date()
        if not isinstance(stored, dict):
            return
        if stored.get("schema_version") != RUNTIME_SCHEMA_VERSION:
            return
        if stored.get("date") != today.isoformat():
            return

        seconds = stored.get("runtime_seconds")
        activations = stored.get("runtime_activations")
        previous = stored.get("runtime_previous")
        if isinstance(seconds, dict):
            self._runtime_seconds = {
                str(key): max(float(value), 0.0)
                for key, value in seconds.items()
                if value is not None
            }
        if isinstance(activations, dict):
            self._runtime_activations = {
                str(key): max(int(value), 0)
                for key, value in activations.items()
                if value is not None
            }
        if isinstance(previous, dict):
            self._runtime_previous = {
                str(key): bool(value) for key, value in previous.items()
            }
        self._runtime_day = today
        self._runtime_last_update = None
        self._runtime_restored = True
        self._runtime_last_saved_at = stored.get("saved_at")

    async def _async_save_runtime_state(self) -> None:
        now = dt_util.now()
        payload = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "date": self._runtime_day.isoformat(),
            "runtime_seconds": dict(self._runtime_seconds),
            "runtime_activations": dict(self._runtime_activations),
            "runtime_previous": dict(self._runtime_previous),
            "saved_at": now.isoformat(),
        }
        await self._runtime_store.async_save(payload)
        self._runtime_dirty_refreshes = 0
        self._runtime_last_saved_at = payload["saved_at"]

    async def async_prepare_unload(self) -> None:
        await self._async_save_runtime_state()
        await super().async_prepare_unload()

    def _apply_runtime_tracking(self, devices: list[dict[str, Any]], now: Any) -> None:
        super()._apply_runtime_tracking(devices, now)
        self._runtime_dirty_refreshes += 1

    def _apply_daily_minimum_start_gates(self, data: dict[str, Any], now: Any) -> None:
        """Prevent early battery/grid starts for daily-minimum loads."""
        configs = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("managed_device_configs") or [])
        }
        solar_after_house_w = float(data.get("solar_after_house_w") or 0.0)
        pv_potential_after_house_w = float(data.get("pv_potential_after_house_w") or 0.0)

        for decision in data.get("dry_run_decisions") or []:
            if not decision.get("would_start"):
                continue
            subentry_id = str(decision.get("subentry_id") or "")
            source = configs.get(subentry_id) or {}
            minimum = float(source.get("min_daily_runtime_minutes") or 0.0)
            remaining = float(source.get("remaining_min_daily_runtime_minutes") or 0.0)
            if minimum <= 0 or remaining <= 0:
                continue

            defer, reason, deadline_pressure = should_defer_daily_minimum_start(
                now=now,
                remaining_minimum_minutes=remaining,
                nominal_power_w=float(decision.get("nominal_power_w") or 0.0),
                solar_after_house_w=solar_after_house_w,
                pv_potential_after_house_w=pv_potential_after_house_w,
                end_before=source.get("end_before"),
            )
            decision["daily_minimum_deadline_pressure"] = deadline_pressure
            decision["daily_minimum_pv_first"] = True
            if defer:
                decision["would_start"] = False
                decision["decision"] = "waiting_daily_minimum_solar"
                decision["reason"] = reason
            elif deadline_pressure:
                decision["reason"] = f"{decision.get('reason') or ''} {reason}".strip()

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        self._apply_daily_minimum_start_gates(data, now)
        await super()._async_apply_real_control(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        if self._runtime_dirty_refreshes >= RUNTIME_SAVE_EVERY_REFRESHES:
            await self._async_save_runtime_state()
        data["runtime_state_persistent"] = True
        data["runtime_state_restored_after_restart"] = self._runtime_restored
        data["runtime_state_last_saved_at"] = self._runtime_last_saved_at
        data["daily_minimum_policy"] = "pv_first_then_deadline_fallback"
        return data

"""Casa ES Energy Manager v1.5.2 persistent wall-clock time semantics.

Home Assistant restarts/reloads must not reset or invent appliance timers.
This layer persists real ON/OFF transition timestamps and reconciles daily
runtime across HA downtime when a device was active before shutdown and is
still active when HA returns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .coordinator_v151 import CasaESEnergyCoordinator as V151Coordinator
from .device_dry_run import _is_running, _state_active

TEMPORAL_STORAGE_VERSION = 1
TEMPORAL_SCHEMA_VERSION = 1


class CasaESEnergyCoordinator(V151Coordinator):
    """v1.5.2 controller with restart-transparent appliance timing."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._temporal_store: Store[dict[str, Any]] = Store(
            hass,
            TEMPORAL_STORAGE_VERSION,
            f"casa_es_energy_manager.{entry.entry_id}.temporal_state",
        )
        self._temporal_dirty = False
        self._temporal_last_saved_at: str | None = None
        self._startup_prior_active: dict[str, bool] = {}
        self._downtime_runtime_reconciled = False
        self._downtime_runtime_added_seconds: dict[str, float] = {}

    @staticmethod
    def _parse_dt(value: Any) -> Any | None:
        if not value:
            return None
        parsed = dt_util.parse_datetime(str(value))
        if parsed is None:
            return None
        return dt_util.as_utc(parsed)

    async def async_initialize(self) -> None:
        await super().async_initialize()
        stored = await self._temporal_store.async_load()
        if not isinstance(stored, dict):
            return
        if stored.get("schema_version") != TEMPORAL_SCHEMA_VERSION:
            return

        observed = stored.get("observed_entity_active")
        transitions = stored.get("last_real_transition_at")
        if isinstance(observed, dict):
            self._observed_entity_active = {
                str(key): bool(value) for key, value in observed.items()
            }
            self._startup_prior_active = dict(self._observed_entity_active)
        if isinstance(transitions, dict):
            restored: dict[str, Any] = {}
            for key, value in transitions.items():
                parsed = self._parse_dt(value)
                if parsed is not None:
                    restored[str(key)] = parsed
            self._last_real_transition_at = restored
        self._temporal_last_saved_at = stored.get("saved_at")

    async def _async_save_temporal_state(self) -> None:
        now = dt_util.utcnow()
        payload = {
            "schema_version": TEMPORAL_SCHEMA_VERSION,
            "observed_entity_active": dict(self._observed_entity_active),
            "last_real_transition_at": {
                key: value.isoformat()
                for key, value in self._last_real_transition_at.items()
                if value is not None
            },
            "saved_at": now.isoformat(),
        }
        await self._temporal_store.async_save(payload)
        self._temporal_dirty = False
        self._temporal_last_saved_at = payload["saved_at"]

    async def async_prepare_unload(self) -> None:
        await self._async_save_temporal_state()
        await super().async_prepare_unload()

    def _managed_device_snapshots(self) -> list[dict[str, Any]]:
        """Use persisted absolute transitions instead of HA metadata clocks."""
        devices = super(V151Coordinator, self)._managed_device_snapshots()
        now = dt_util.utcnow()

        for item in devices:
            subentry_id = str(item.get("subentry_id") or "")
            if not subentry_id:
                item["seconds_since_change"] = None
                continue

            active = _state_active(item.get("state"))
            previous = self._observed_entity_active.get(subentry_id)

            if previous is None:
                # No known real transition exists yet. Establish baseline only.
                self._observed_entity_active[subentry_id] = active
                item["seconds_since_change"] = None
                self._temporal_dirty = True
            elif previous != active:
                # A real state change was observed while HA was running. Persist
                # the absolute wall-clock timestamp so reboots cannot reset it.
                self._observed_entity_active[subentry_id] = active
                self._last_real_transition_at[subentry_id] = now
                item["seconds_since_change"] = 0.0
                self._temporal_dirty = True
            else:
                changed_at = self._last_real_transition_at.get(subentry_id)
                item["seconds_since_change"] = (
                    max((now - changed_at).total_seconds(), 0.0)
                    if changed_at is not None
                    else None
                )

            # Keep v1.5.1 climate migration: installed legacy 20/20 becomes
            # 20-minute minimum ON and 5-minute minimum OFF.
            if str(item.get("device_type") or "") == "climate":
                try:
                    min_on = float(item.get("min_on_minutes") or 0.0)
                    min_off = float(item.get("min_off_minutes") or 0.0)
                except (TypeError, ValueError):
                    min_on = min_off = 0.0
                if abs(min_on - 20.0) < 1e-9 and abs(min_off - 20.0) < 1e-9:
                    item["min_off_minutes"] = 5.0
                    item["anti_cycle_profile_migrated_v151"] = True

            item["anti_cycle_transition_source"] = "persistent_real_transition"

        return devices

    def _runtime_reconcile_start(self, now: Any) -> Any | None:
        """Return the wall-clock point from which missing runtime can be counted."""
        # Same-day runtime store is the strongest source because its accumulated
        # counters are known to be complete up to saved_at.
        if self._runtime_restored:
            parsed = self._parse_dt(self._runtime_last_saved_at)
            if parsed is not None:
                return parsed

        # If HA was down across midnight, yesterday's daily runtime is purposely
        # not restored. When a device is continuously ON across the boundary,
        # today's runtime starts at local midnight.
        temporal_saved = self._parse_dt(self._temporal_last_saved_at)
        if temporal_saved is None:
            return None
        local_now = dt_util.as_local(now)
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_utc = dt_util.as_utc(local_midnight)
        if temporal_saved < midnight_utc:
            return midnight_utc
        return temporal_saved

    def _apply_runtime_tracking(self, devices: list[dict[str, Any]], now: Any) -> None:
        """Reconcile continuous ON time that elapsed while HA was unavailable."""
        if not self._downtime_runtime_reconciled:
            start = self._runtime_reconcile_start(now)
            if start is not None and now > start:
                elapsed = max((now - start).total_seconds(), 0.0)
                for item in devices:
                    subentry_id = str(item.get("subentry_id") or "")
                    if not subentry_id:
                        continue
                    prior_active = self._startup_prior_active.get(subentry_id)
                    shared = bool(item.get("adaptive_shared_power_sensor"))
                    running_now = _is_running(
                        item.get("state"),
                        item.get("current_power_w"),
                        shared_power_sensor=shared,
                    )
                    if prior_active is True and running_now:
                        self._runtime_seconds[subentry_id] = (
                            self._runtime_seconds.get(subentry_id, 0.0) + elapsed
                        )
                        self._downtime_runtime_added_seconds[subentry_id] = elapsed

            # Prevent the inherited tracker from adding the same wall time again.
            self._runtime_last_update = now
            self._downtime_runtime_reconciled = True

        super()._apply_runtime_tracking(devices, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        if self._temporal_dirty:
            await self._async_save_temporal_state()
        data["v152_persistent_wall_clock_timers"] = True
        data["temporal_state_last_saved_at"] = self._temporal_last_saved_at
        data["downtime_runtime_reconciled"] = self._downtime_runtime_reconciled
        data["downtime_runtime_added_seconds"] = {
            key: round(value, 1)
            for key, value in self._downtime_runtime_added_seconds.items()
        }
        data["timing_semantics"] = (
            "absolute_persistent_transitions_and_continuous_runtime_across_ha_downtime"
        )
        return data

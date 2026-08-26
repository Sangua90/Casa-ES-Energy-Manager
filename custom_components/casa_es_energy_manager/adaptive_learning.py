"""Adaptive power profiling for variable managed loads."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    ADAPTIVE_ACTIVE_POWER_THRESHOLD_W,
    ADAPTIVE_ESTIMATE_MAX_MEAN_FACTOR,
    ADAPTIVE_PROFILE_MIN_SAMPLES,
    ADAPTIVE_SAVE_EVERY_OBSERVATIONS,
    CONF_DEVICE_MODE_CLIMATE_ENTITY,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_CLIMATE,
)

STORAGE_VERSION = 1
# Keep schema v2 so the valid profiles collected by v1.1.1 survive the v1.2
# update. v1.2 adds mode metadata without changing the stored statistics shape.
PROFILE_SCHEMA_VERSION = 2
GENERAL_MODE = "general"
_INACTIVE_STATES = {"", "off", "idle", "standby", "unknown", "unavailable", "none"}


class AdaptivePowerLearner:
    """Learn persistent per-device power statistics from a real power sensor.

    Active samples are accepted only while the managed entity is really active.
    Shared meters are never learned automatically because their watts cannot be
    attributed safely to one load. Climate/PDC devices can use a separate climate
    entity only as a mode reference while keeping a switch as the real command.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"casa_es_energy_manager.{entry_id}.adaptive_profiles"
        )
        self.data: dict[str, Any] = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "devices": {},
        }
        self._observations_since_save = 0

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if (
            isinstance(stored, dict)
            and stored.get("schema_version") == PROFILE_SCHEMA_VERSION
            and isinstance(stored.get("devices"), dict)
        ):
            self.data = stored
        else:
            self.data = {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "devices": {},
            }

    async def async_save(self) -> None:
        await self.store.async_save(self.data)
        self._observations_since_save = 0

    @staticmethod
    def _is_climate_profiled(device: dict[str, Any]) -> bool:
        entity_id = str(device.get("entity_id") or "")
        return bool(
            str(device.get(CONF_DEVICE_TYPE) or "") == DEVICE_TYPE_CLIMATE
            or entity_id.startswith("climate.")
            or device.get(CONF_DEVICE_MODE_CLIMATE_ENTITY)
        )

    @classmethod
    def mode_for(cls, device: dict[str, Any]) -> str:
        """Return the learning bucket for this device right now."""
        profile_mode = str(device.get("profile_mode") or "").strip().lower()
        if profile_mode:
            return profile_mode
        entity_id = str(device.get("entity_id") or "")
        if entity_id.startswith("climate."):
            return str(device.get("hvac_mode") or device.get("state") or "unknown").lower()
        return GENERAL_MODE

    @staticmethod
    def is_actively_running(device: dict[str, Any]) -> bool:
        """Return whether the managed entity itself is actively operating."""
        entity_id = str(device.get("entity_id") or "")
        state = str(device.get("state") or "").strip().lower()
        if entity_id.startswith("climate."):
            action = str(device.get("hvac_action") or "").strip().lower()
            if action:
                return action not in _INACTIVE_STATES
            return state not in _INACTIVE_STATES
        return state == "on"

    async def async_observe(self, devices: list[dict[str, Any]]) -> None:
        changed = False
        for device in devices:
            if not device.get("adaptive_power_profile"):
                continue
            if not device.get("power_sensor"):
                continue
            if device.get("adaptive_shared_power_sensor"):
                continue
            # A switch configured as Climate/PDC must not put unknown-mode watts
            # into a generic bucket when its selected climate reference is down.
            if device.get("mode_reference_required") and not device.get(
                "mode_reference_available"
            ):
                continue

            entity_id = str(device.get("entity_id") or "")
            if not entity_id:
                continue
            power = device.get("current_power_w")
            try:
                power_w = max(float(power), 0.0)
            except (TypeError, ValueError):
                continue

            mode = self.mode_for(device)
            action = str(
                device.get("profile_hvac_action")
                or device.get("hvac_action")
                or device.get("state")
                or "unknown"
            )
            dev = self.data.setdefault("devices", {}).setdefault(entity_id, {"modes": {}})
            stats = dev.setdefault("modes", {}).setdefault(
                mode,
                {
                    "samples": 0,
                    "active_samples": 0,
                    "mean_w": 0.0,
                    "m2": 0.0,
                    "min_w": None,
                    "max_w": 0.0,
                    "last_power_w": 0.0,
                    "last_action": action,
                },
            )
            stats["samples"] = int(stats.get("samples", 0)) + 1
            stats["last_power_w"] = round(power_w, 1)
            stats["last_action"] = action

            if (
                self.is_actively_running(device)
                and power_w >= ADAPTIVE_ACTIVE_POWER_THRESHOLD_W
            ):
                n = int(stats.get("active_samples", 0)) + 1
                old_mean = float(stats.get("mean_w", 0.0))
                delta = power_w - old_mean
                new_mean = old_mean + delta / n
                delta2 = power_w - new_mean
                stats["active_samples"] = n
                stats["mean_w"] = new_mean
                stats["m2"] = float(stats.get("m2", 0.0)) + delta * delta2
                old_min = stats.get("min_w")
                stats["min_w"] = power_w if old_min is None else min(float(old_min), power_w)
                stats["max_w"] = max(float(stats.get("max_w", 0.0)), power_w)
            changed = True

        if changed:
            self._observations_since_save += 1
            if self._observations_since_save >= ADAPTIVE_SAVE_EVERY_OBSERVATIONS:
                await self.async_save()

    def profile_for(
        self,
        entity_id: str,
        mode: str,
        fallback_w: float,
    ) -> dict[str, Any]:
        """Return a conservative learned admission estimate."""
        stats = (
            self.data.get("devices", {})
            .get(entity_id, {})
            .get("modes", {})
            .get(mode)
        )
        if not isinstance(stats, dict):
            return {
                "status": "learning",
                "samples": 0,
                "active_samples": 0,
                "estimated_power_w": round(max(fallback_w, 0.0), 1),
            }

        active = int(stats.get("active_samples", 0))
        mean = float(stats.get("mean_w", 0.0))
        m2 = float(stats.get("m2", 0.0))
        maximum = float(stats.get("max_w", 0.0))
        std = math.sqrt(m2 / (active - 1)) if active > 1 else 0.0
        outlier_limited = False

        if active < ADAPTIVE_PROFILE_MIN_SAMPLES:
            estimate = max(fallback_w, maximum)
            status = "learning"
        else:
            raw_high = max(mean * 1.15, mean + 2.0 * std)
            estimate = raw_high
            if maximum > 0:
                estimate = min(estimate, maximum)
            if mean > 0:
                robust_cap = mean * ADAPTIVE_ESTIMATE_MAX_MEAN_FACTOR
                if estimate > robust_cap:
                    estimate = robust_cap
                    outlier_limited = True
            estimate = max(estimate, mean)
            status = "ready"

        return {
            "status": status,
            "samples": int(stats.get("samples", 0)),
            "active_samples": active,
            "mean_w": round(mean, 1),
            "std_w": round(std, 1),
            "min_w": round(float(stats.get("min_w") or 0.0), 1),
            "max_w": round(maximum, 1),
            "last_power_w": round(float(stats.get("last_power_w", 0.0)), 1),
            "last_action": stats.get("last_action"),
            "estimated_power_w": round(max(estimate, 0.0), 1),
            "outlier_limited": outlier_limited,
        }

    def admission_profile_for(
        self, device: dict[str, Any], fallback_w: float
    ) -> dict[str, Any]:
        """Return the profile safe to use for a future admission decision."""
        if device.get("adaptive_shared_power_sensor"):
            return {
                "status": "shared_power_sensor",
                "samples": 0,
                "active_samples": 0,
                "estimated_power_w": round(max(fallback_w, 0.0), 1),
            }

        entity_id = str(device.get("entity_id") or "")
        if not self._is_climate_profiled(device):
            return self.profile_for(entity_id, GENERAL_MODE, fallback_w)

        if device.get("mode_reference_required") and not device.get(
            "mode_reference_available"
        ):
            general = self.profile_for(entity_id, GENERAL_MODE, fallback_w)
            general["status"] = "mode_reference_unavailable"
            general["source_mode"] = GENERAL_MODE
            return general

        current_mode = self.mode_for(device).strip().lower()
        if current_mode not in _INACTIVE_STATES:
            profile = self.profile_for(entity_id, current_mode, fallback_w)
            # Existing switch-based v1.1 profiles lived in `general`. Keep that
            # mature estimate as a safe bridge while a new cool/heat bucket is
            # collecting its first samples.
            if current_mode != GENERAL_MODE and profile.get("status") == "learning":
                general = self.profile_for(entity_id, GENERAL_MODE, fallback_w)
                if general.get("status") == "ready":
                    profile["estimated_power_w"] = round(
                        max(
                            float(profile.get("estimated_power_w") or 0.0),
                            float(general.get("estimated_power_w") or 0.0),
                        ),
                        1,
                    )
                    profile["fallback_mode"] = GENERAL_MODE
            return profile

        # A climate/PDC currently off must never size its next start from an
        # off/standby profile. Prefer the best learned active mode. If only the
        # old general profile exists, it is a valid conservative bridge.
        modes = (
            self.data.get("devices", {})
            .get(entity_id, {})
            .get("modes", {})
        )
        active_modes = [
            (mode, stats)
            for mode, stats in modes.items()
            if str(mode).strip().lower() not in _INACTIVE_STATES
            and isinstance(stats, dict)
        ]
        if not active_modes:
            return self.profile_for(entity_id, "__active__", fallback_w)

        best_mode, _ = max(
            active_modes,
            key=lambda pair: int(pair[1].get("active_samples", 0)),
        )
        profile = self.profile_for(entity_id, str(best_mode), fallback_w)
        profile["source_mode"] = str(best_mode)
        return profile

    def export(self) -> dict[str, Any]:
        """Return diagnostics-friendly learned profiles."""
        return self.data

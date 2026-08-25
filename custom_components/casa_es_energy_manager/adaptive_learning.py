"""Adaptive power profiling for variable managed loads."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    ADAPTIVE_ACTIVE_POWER_THRESHOLD_W,
    ADAPTIVE_PROFILE_MIN_SAMPLES,
    ADAPTIVE_SAVE_EVERY_OBSERVATIONS,
)

STORAGE_VERSION = 1
GENERAL_MODE = "general"


class AdaptivePowerLearner:
    """Learn per-device power statistics from a real power sensor.

    A climate entity is useful when the controlled entity itself is a climate,
    because Casa ES can then keep separate profiles for HVAC modes. It is not a
    requirement: every managed device with a real power sensor can learn a
    general variable-power profile from the observed watts alone.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"casa_es_energy_manager.{entry_id}.adaptive_profiles"
        )
        self.data: dict[str, Any] = {"devices": {}}
        self._observations_since_save = 0

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("devices"), dict):
            self.data = stored

    async def async_save(self) -> None:
        await self.store.async_save(self.data)
        self._observations_since_save = 0

    @staticmethod
    def mode_for(device: dict[str, Any]) -> str:
        entity_id = str(device.get("entity_id") or "")
        if entity_id.startswith("climate."):
            return str(device.get("hvac_mode") or device.get("state") or "unknown")
        return GENERAL_MODE

    async def async_observe(self, devices: list[dict[str, Any]]) -> None:
        changed = False
        for device in devices:
            if not device.get("adaptive_power_profile"):
                continue
            if not device.get("power_sensor"):
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
                device.get("hvac_action")
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

            if power_w >= ADAPTIVE_ACTIVE_POWER_THRESHOLD_W:
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

        if active < ADAPTIVE_PROFILE_MIN_SAMPLES:
            estimate = max(fallback_w, maximum)
            status = "learning"
        else:
            high = mean + 2.0 * std
            estimate = max(mean * 1.15, high)
            if maximum > 0:
                estimate = min(max(estimate, mean), maximum)
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
        }

    def export(self) -> dict[str, Any]:
        """Return diagnostics-friendly learned profiles."""
        return self.data

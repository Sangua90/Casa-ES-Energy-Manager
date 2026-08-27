"""Persistent thermal learning for domestic hot-water storage in v1.5."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

STORE_VERSION = 1
SCHEMA_VERSION = 1
SAVE_EVERY = 24
MIN_SAMPLE_SECONDS = 20.0
MAX_SAMPLE_SECONDS = 900.0
MAX_PASSIVE_LOSS_C_PER_H = 2.0
MIN_HEATING_GAIN_C_PER_H = 0.05


def _f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean_update(bucket: dict[str, Any], key: str, value: float) -> None:
    n_key = f"{key}_samples"
    old_n = int(bucket.get(n_key, 0))
    old_mean = float(bucket.get(key, 0.0))
    new_n = old_n + 1
    bucket[key] = old_mean + (value - old_mean) / new_n
    bucket[n_key] = new_n


class ThermalLearnerV15:
    """Learn boiler heating rates, power per source, standby loss and draw habits."""

    def __init__(self, hass: Any, entry_id: str) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(
            hass,
            STORE_VERSION,
            f"casa_es_energy_manager.{entry_id}.thermal_profiles",
        )
        self.data: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "devices": {}}
        self._last: dict[str, dict[str, Any]] = {}
        self._dirty = 0

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if (
            isinstance(stored, dict)
            and stored.get("schema_version") == SCHEMA_VERSION
            and isinstance(stored.get("devices"), dict)
        ):
            self.data = stored

    async def async_save(self) -> None:
        await self.store.async_save(self.data)
        self._dirty = 0

    def profile(self, subentry_id: str) -> dict[str, Any]:
        source = self.data.get("devices", {}).get(subentry_id, {})
        profile = dict(source) if isinstance(source, dict) else {}
        for field in (
            "heat_pump_c_per_h",
            "resistance_c_per_h",
            "heat_pump_mean_power_w",
            "resistance_mean_power_w",
            "standby_loss_c_per_h",
        ):
            if field in profile:
                profile[field] = round(float(profile[field]), 3)

        hp_rate = _f(profile.get("heat_pump_c_per_h"))
        hp_power = _f(profile.get("heat_pump_mean_power_w"))
        res_rate = _f(profile.get("resistance_c_per_h"))
        res_power = _f(profile.get("resistance_mean_power_w"))
        profile["heat_pump_kwh_per_c"] = (
            round((hp_power / 1000.0) / hp_rate, 4)
            if hp_power and hp_rate and hp_rate > 0
            else None
        )
        profile["resistance_kwh_per_c"] = (
            round((res_power / 1000.0) / res_rate, 4)
            if res_power and res_rate and res_rate > 0
            else None
        )
        return profile

    def expected_draw_c(self, subentry_id: str, start_hour: int, end_hour: int = 24) -> float:
        profile = self.data.get("devices", {}).get(subentry_id, {})
        hourly = profile.get("draw_by_hour", {}) if isinstance(profile, dict) else {}
        total = 0.0
        for hour in range(max(start_hour, 0), min(end_hour, 24)):
            bucket = hourly.get(str(hour), {}) if isinstance(hourly, dict) else {}
            samples = int(bucket.get("days", 0)) if isinstance(bucket, dict) else 0
            if samples > 0:
                total += max(float(bucket.get("mean_drop_c", 0.0)), 0.0)
        return total

    async def async_observe(self, devices: list[dict[str, Any]]) -> None:
        now = dt_util.now()
        changed = False
        active_ids: set[str] = set()

        for device in devices:
            subentry_id = str(device.get("subentry_id") or "")
            if not subentry_id or device.get("device_type") != "thermal_storage":
                continue
            active_ids.add(subentry_id)
            if device.get("management_mode") != "auto" or device.get("learning_excluded"):
                self._last.pop(subentry_id, None)
                continue

            temp = _f(device.get("thermal_current_temperature_c"))
            if temp is None:
                self._last.pop(subentry_id, None)
                continue

            previous = self._last.get(subentry_id)
            current = {
                "at": now,
                "temp": temp,
                "heating": bool(device.get("thermal_heating")),
                "boost": bool(device.get("thermal_boost_active")),
                "power_w": _f(device.get("current_power_w")),
            }
            self._last[subentry_id] = current
            if not previous:
                continue

            elapsed = (now - previous["at"]).total_seconds()
            if elapsed < MIN_SAMPLE_SECONDS or elapsed > MAX_SAMPLE_SECONDS:
                continue
            delta = temp - float(previous["temp"])
            rate = delta * 3600.0 / elapsed
            dev = self.data.setdefault("devices", {}).setdefault(
                subentry_id,
                {"draw_by_hour": {}},
            )

            heating = bool(previous.get("heating")) and bool(current.get("heating"))
            boost = bool(previous.get("boost")) and bool(current.get("boost"))
            power = current.get("power_w")

            if heating and rate >= MIN_HEATING_GAIN_C_PER_H:
                if boost:
                    _mean_update(dev, "resistance_c_per_h", rate)
                    if power is not None and power > 20:
                        _mean_update(dev, "resistance_mean_power_w", power)
                else:
                    _mean_update(dev, "heat_pump_c_per_h", rate)
                    if power is not None and power > 20:
                        _mean_update(dev, "heat_pump_mean_power_w", power)
                changed = True
                continue

            if not heating and not boost and delta < 0:
                loss_rate = -rate
                if 0 < loss_rate <= MAX_PASSIVE_LOSS_C_PER_H:
                    _mean_update(dev, "standby_loss_c_per_h", loss_rate)
                    changed = True
                elif loss_rate > MAX_PASSIVE_LOSS_C_PER_H:
                    hour = str(now.hour)
                    hourly = dev.setdefault("draw_by_hour", {})
                    bucket = hourly.setdefault(hour, {"days": 0, "mean_drop_c": 0.0})
                    old_n = int(bucket.get("days", 0))
                    new_n = old_n + 1
                    drop = max(-delta, 0.0)
                    bucket["mean_drop_c"] = float(bucket.get("mean_drop_c", 0.0)) + (
                        drop - float(bucket.get("mean_drop_c", 0.0))
                    ) / new_n
                    bucket["days"] = new_n
                    changed = True

        for subentry_id in set(self._last) - active_ids:
            self._last.pop(subentry_id, None)

        if changed:
            self._dirty += 1
            if self._dirty >= SAVE_EVERY:
                await self.async_save()

    def export(self) -> dict[str, Any]:
        return {
            key: self.profile(key)
            for key in self.data.get("devices", {})
        }

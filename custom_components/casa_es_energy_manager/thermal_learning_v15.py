"""Persistent thermal learning for domestic hot-water storage in v1.5."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

STORE_VERSION = 1
SCHEMA_VERSION = 1
SAVE_EVERY = 24
MAX_SAMPLE_SECONDS = 43_200.0
MAX_PASSIVE_LOSS_C_PER_H = 2.0
MIN_HEATING_GAIN_C_PER_H = 0.05
MIN_DRAW_DROP_C = 0.4
RECENT_DRAW_DAYS = 7


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
        # Baselines are intentionally kept until temperature or operating state
        # changes. The coordinator refreshes every few seconds while the Ariston
        # temperature normally changes much more slowly; replacing the baseline
        # on every refresh would make every observation interval too short and
        # prevent thermal learning entirely.
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
        profile["recent_7d_expected_draw_c"] = round(
            self.expected_draw_c_recent(subentry_id, dt_util.now().hour, 24), 2
        )
        profile["recent_7d_days"] = self.recent_draw_days(subentry_id)
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

    def _recent_draw_map(self, subentry_id: str) -> dict[str, Any]:
        profile = self.data.get("devices", {}).get(subentry_id, {})
        recent = profile.get("recent_draw_by_day", {}) if isinstance(profile, dict) else {}
        return recent if isinstance(recent, dict) else {}

    def recent_draw_days(self, subentry_id: str) -> int:
        now = dt_util.now().date()
        cutoff = now - timedelta(days=RECENT_DRAW_DAYS - 1)
        days = 0
        for day in self._recent_draw_map(subentry_id):
            try:
                parsed = dt_util.parse_date(day)
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None and cutoff <= parsed <= now:
                days += 1
        return days

    def expected_draw_c_recent(
        self, subentry_id: str, start_hour: int, end_hour: int = 24
    ) -> float:
        """Return the mean daily draw-induced temperature drop over the last 7 days."""
        now = dt_util.now().date()
        cutoff = now - timedelta(days=RECENT_DRAW_DAYS - 1)
        totals: list[float] = []
        for day, day_data in self._recent_draw_map(subentry_id).items():
            try:
                parsed = dt_util.parse_date(day)
            except (TypeError, ValueError):
                parsed = None
            if parsed is None or parsed < cutoff or parsed > now or not isinstance(day_data, dict):
                continue
            total = 0.0
            for hour in range(max(start_hour, 0), min(end_hour, 24)):
                total += max(float(day_data.get(str(hour), 0.0) or 0.0), 0.0)
            totals.append(total)
        if not totals:
            return 0.0
        return sum(totals) / len(totals)

    @staticmethod
    def _prune_recent_draws(dev: dict[str, Any], now_date: Any) -> None:
        recent = dev.setdefault("recent_draw_by_day", {})
        if not isinstance(recent, dict):
            dev["recent_draw_by_day"] = {}
            return
        cutoff = now_date - timedelta(days=RECENT_DRAW_DAYS - 1)
        for day in list(recent):
            try:
                parsed = dt_util.parse_date(day)
            except (TypeError, ValueError):
                parsed = None
            if parsed is None or parsed < cutoff or parsed > now_date:
                recent.pop(day, None)

    @staticmethod
    def _record_draw(
        dev: dict[str, Any], now: Any, drop: float, while_heating: bool
    ) -> None:
        hour = str(now.hour)
        hourly = dev.setdefault("draw_by_hour", {})
        bucket = hourly.setdefault(hour, {"days": 0, "mean_drop_c": 0.0})
        old_n = int(bucket.get("days", 0))
        new_n = old_n + 1
        bucket["mean_drop_c"] = float(bucket.get("mean_drop_c", 0.0)) + (
            drop - float(bucket.get("mean_drop_c", 0.0))
        ) / new_n
        bucket["days"] = new_n

        recent = dev.setdefault("recent_draw_by_day", {})
        day_bucket = recent.setdefault(now.date().isoformat(), {})
        day_bucket[hour] = float(day_bucket.get(hour, 0.0) or 0.0) + drop

        dev["draw_events"] = int(dev.get("draw_events", 0)) + 1
        if while_heating:
            dev["draw_events_while_heating"] = int(
                dev.get("draw_events_while_heating", 0)
            ) + 1
        dev["last_draw_at"] = now.isoformat()
        dev["last_draw_drop_c"] = round(drop, 3)
        dev["last_draw_while_heating"] = bool(while_heating)

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

            current = {
                "at": now,
                "temp": temp,
                "heating": bool(device.get("thermal_heating")),
                "boost": bool(device.get("thermal_boost_active")),
                "power_w": _f(device.get("current_power_w")),
            }
            previous = self._last.get(subentry_id)
            if not previous:
                self._last[subentry_id] = current
                continue

            # A source-state transition starts a fresh baseline. This prevents a
            # temperature change spanning OFF->PDC or PDC->Boost from being
            # attributed to the wrong source.
            if (
                bool(previous.get("heating")) != bool(current.get("heating"))
                or bool(previous.get("boost")) != bool(current.get("boost"))
            ):
                self._last[subentry_id] = current
                continue

            # Keep the timestamp of the last actual temperature change. Ariston
            # commonly reports quantized temperatures, so identical 5-second
            # refreshes must not erase the useful elapsed interval.
            delta = temp - float(previous["temp"])
            if abs(delta) < 0.001:
                continue

            elapsed = (now - previous["at"]).total_seconds()
            self._last[subentry_id] = current
            if elapsed <= 0 or elapsed > MAX_SAMPLE_SECONDS:
                continue

            rate = delta * 3600.0 / elapsed
            dev = self.data.setdefault("devices", {}).setdefault(
                subentry_id,
                {"draw_by_hour": {}, "recent_draw_by_day": {}},
            )
            self._prune_recent_draws(dev, now.date())
            dev["temperature_change_samples"] = int(
                dev.get("temperature_change_samples", 0)
            ) + 1
            dev["last_temperature_change_at"] = now.isoformat()
            dev["last_temperature_delta_c"] = round(delta, 3)
            dev["last_temperature_rate_c_per_h"] = round(rate, 3)

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
                dev["heating_gain_samples"] = int(dev.get("heating_gain_samples", 0)) + 1
                changed = True
                continue

            if not boost and delta < 0:
                loss_rate = -rate
                drop = max(-delta, 0.0)

                # A slow fall while idle is standby loss. A faster fall is a hot
                # water draw. If the native PDC is already running, a negative
                # temperature change still means demand exceeded heating input,
                # so it must be learned as a draw instead of being discarded.
                if not heating and 0 < loss_rate <= MAX_PASSIVE_LOSS_C_PER_H:
                    _mean_update(dev, "standby_loss_c_per_h", loss_rate)
                    dev["passive_loss_samples"] = int(
                        dev.get("passive_loss_samples", 0)
                    ) + 1
                    changed = True
                elif drop >= MIN_DRAW_DROP_C and (
                    heating or loss_rate > MAX_PASSIVE_LOSS_C_PER_H
                ):
                    self._record_draw(dev, now, drop, heating)
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

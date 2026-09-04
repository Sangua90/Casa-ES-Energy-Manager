"""v1.5.15+ thermal learner persistence and draw hardening."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .thermal_learning_v15 import (
    DRAW_HEATING_START_MIN_ABOVE_BASE_C,
    DRAW_HEATING_START_WINDOW_SECONDS,
    MAX_PASSIVE_LOSS_C_PER_H,
    ThermalLearnerV15,
    _f,
    _mean_remove,
)

DRAW_EPISODE_MAX_GAP_SECONDS = 5_400.0


class ThermalLearnerV1515(ThermalLearnerV15):
    """Persist every learned change and aggregate quantized draw episodes.

    Ariston reports temperature in coarse steps. A real shower can therefore be
    observed as several slow -1 C changes, each of which looks like standby loss
    in isolation. Keep those consecutive provisional passive steps as one episode.
    If native PDC recovery then starts shortly afterwards while the tank is still
    above its base target, collapse the whole episode into the single candidate
    consumed by the base learner. This lets the base learner reclassify the full
    shower drop instead of only the last 1 C step.
    """

    def __init__(self, hass: Any, entry_id: str) -> None:
        super().__init__(hass, entry_id)
        self._draw_episode: dict[str, dict[str, Any]] = {}

    def _track_quantized_episode(self, device: dict[str, Any], now: Any) -> None:
        subentry_id = str(device.get("subentry_id") or "")
        if not subentry_id or device.get("device_type") != "thermal_storage":
            return
        if device.get("management_mode") != "auto" or device.get("learning_excluded"):
            self._draw_episode.pop(subentry_id, None)
            return

        temp = _f(device.get("thermal_current_temperature_c"))
        previous = self._last.get(subentry_id)
        if temp is None or previous is None:
            return

        current_heating = bool(device.get("thermal_heating"))
        current_boost = bool(device.get("thermal_boost_active"))
        previous_heating = bool(previous.get("heating"))
        previous_boost = bool(previous.get("boost"))

        # Positive temperature movement or Boost means the previous cooling
        # episode is over and must never bleed into a later unrelated event.
        delta = temp - float(previous.get("temp", temp))
        if delta > 0 or current_boost or previous_boost:
            self._draw_episode.pop(subentry_id, None)
            return

        # Before the base learner handles a native-PDC start, replace its single
        # most-recent negative candidate with the cumulative shower episode and
        # undo all earlier provisional standby samples. The base learner will
        # then undo the final sample and record one draw with the whole drop.
        if not previous_heating and current_heating and not current_boost:
            episode = self._draw_episode.get(subentry_id)
            base_temp = _f(device.get("thermal_base_temperature_c")) or 53.0
            if episode is not None:
                since_last = (now - episode["last_at"]).total_seconds()
                if (
                    0 <= since_last <= DRAW_HEATING_START_WINDOW_SECONDS
                    and temp >= base_temp + DRAW_HEATING_START_MIN_ABOVE_BASE_C
                ):
                    rates = list(episode.get("passive_rates", []))
                    dev = self.data.setdefault("devices", {}).setdefault(
                        subentry_id,
                        {"draw_by_hour": {}, "recent_draw_by_day": {}},
                    )
                    # All but the final passive sample are reversed here. The
                    # base learner reverses the final one from _recent_negative.
                    for rate in rates[:-1]:
                        _mean_remove(dev, "standby_loss_c_per_h", float(rate))
                        dev["passive_loss_samples"] = max(
                            int(dev.get("passive_loss_samples", 0)) - 1,
                            0,
                        )
                    if rates:
                        self._recent_negative[subentry_id] = {
                            "at": episode["last_at"],
                            "drop": float(episode.get("drop", 0.0)),
                            "loss_rate": float(rates[-1]),
                            "classified_passive": True,
                        }
                    dev["last_draw_episode_steps"] = len(rates)
                    dev["last_draw_episode_drop_c"] = round(
                        float(episode.get("drop", 0.0)), 3
                    )
                    dev["last_draw_episode_started_at"] = episode["started_at"].isoformat()
                    dev["last_draw_episode_last_at"] = episode["last_at"].isoformat()
            self._draw_episode.pop(subentry_id, None)
            return

        # Track only the exact class of negative sample that the base learner is
        # about to learn provisionally as passive standby loss.
        if delta >= 0 or current_heating or current_boost:
            return
        elapsed = (now - previous["at"]).total_seconds()
        if elapsed <= 0:
            return
        loss_rate = (-delta) * 3600.0 / elapsed
        if not (0 < loss_rate <= MAX_PASSIVE_LOSS_C_PER_H):
            self._draw_episode.pop(subentry_id, None)
            return

        episode = self._draw_episode.get(subentry_id)
        if episode is not None:
            gap = (now - episode["last_at"]).total_seconds()
            if gap > DRAW_EPISODE_MAX_GAP_SECONDS:
                episode = None
        if episode is None:
            episode = {
                "started_at": now,
                "last_at": now,
                "drop": 0.0,
                "passive_rates": [],
            }
            self._draw_episode[subentry_id] = episode

        episode["last_at"] = now
        episode["drop"] = float(episode.get("drop", 0.0)) + max(-delta, 0.0)
        episode.setdefault("passive_rates", []).append(loss_rate)

    async def async_observe(self, devices: list[dict[str, Any]]) -> None:
        now = dt_util.now()
        for device in devices:
            self._track_quantized_episode(device, now)

        await super().async_observe(devices)

        active_ids = {
            str(device.get("subentry_id") or "")
            for device in devices
            if device.get("device_type") == "thermal_storage"
        }
        for subentry_id in set(self._draw_episode) - active_ids:
            self._draw_episode.pop(subentry_id, None)

        # v1.5.15 persistence hardening: every real learned change is durable.
        if self._dirty > 0:
            await self.async_save()

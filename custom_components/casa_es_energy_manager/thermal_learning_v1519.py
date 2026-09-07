"""v1.5.19 thermal learner with long, absence-resistant demand memory."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .thermal_learning_v1515 import ThermalLearnerV1515

THERMAL_HISTORY_DAYS = 30


def _age_weight(age_days: int) -> float:
    """Weight recent observed-demand days more than older ones without forgetting them."""
    if age_days <= 6:
        return 1.0
    if age_days <= 13:
        return 0.75
    if age_days <= 20:
        return 0.50
    return 0.30


class ThermalLearnerV1519(ThermalLearnerV1515):
    """Keep 30 days of draw history and use an age-weighted observed-day mean.

    Days with no detected draw are intentionally absent from recent_draw_by_day,
    so a weekend or a week away from home does not dilute the learned household
    demand toward zero. Older observed days remain useful for up to 30 days but
    progressively carry less weight than recent behaviour.
    """

    def recent_draw_days(self, subentry_id: str) -> int:
        now = dt_util.now().date()
        cutoff = now - timedelta(days=THERMAL_HISTORY_DAYS - 1)
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
        """Return an age-weighted mean draw over observed days in the last 30 days."""
        now = dt_util.now().date()
        cutoff = now - timedelta(days=THERMAL_HISTORY_DAYS - 1)
        weighted_total = 0.0
        total_weight = 0.0

        for day, day_data in self._recent_draw_map(subentry_id).items():
            try:
                parsed = dt_util.parse_date(day)
            except (TypeError, ValueError):
                parsed = None
            if parsed is None or parsed < cutoff or parsed > now or not isinstance(day_data, dict):
                continue

            daily_total = 0.0
            for hour in range(max(start_hour, 0), min(end_hour, 24)):
                daily_total += max(float(day_data.get(str(hour), 0.0) or 0.0), 0.0)

            # Only observed-demand days exist in the map. Missing dates are not
            # interpreted as zero consumption, protecting the model from absences.
            age_days = (now - parsed).days
            weight = _age_weight(age_days)
            weighted_total += daily_total * weight
            total_weight += weight

        if total_weight <= 0:
            return 0.0
        return weighted_total / total_weight

    @staticmethod
    def _prune_recent_draws(dev: dict[str, Any], now_date: Any) -> None:
        recent = dev.setdefault("recent_draw_by_day", {})
        if not isinstance(recent, dict):
            dev["recent_draw_by_day"] = {}
            return
        cutoff = now_date - timedelta(days=THERMAL_HISTORY_DAYS - 1)
        for day in list(recent):
            try:
                parsed = dt_util.parse_date(day)
            except (TypeError, ValueError):
                parsed = None
            if parsed is None or parsed < cutoff or parsed > now_date:
                recent.pop(day, None)

    def profile(self, subentry_id: str) -> dict[str, Any]:
        profile = super().profile(subentry_id)
        expected = round(
            self.expected_draw_c_recent(subentry_id, dt_util.now().hour, 24), 2
        )
        days = self.recent_draw_days(subentry_id)
        profile["recent_30d_weighted_expected_draw_c"] = expected
        profile["recent_30d_observed_days"] = days
        profile["thermal_history_days"] = THERMAL_HISTORY_DAYS
        profile["thermal_history_weighting"] = {
            "days_0_6": 1.0,
            "days_7_13": 0.75,
            "days_14_20": 0.50,
            "days_21_29": 0.30,
            "missing_days_count_as_zero": False,
        }
        # Preserve the legacy diagnostic keys for compatibility, but make their
        # values reflect the new 30-day weighted model.
        profile["recent_7d_expected_draw_c"] = expected
        profile["recent_7d_days"] = days
        return profile

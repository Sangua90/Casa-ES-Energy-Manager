"""Casa ES Energy Manager v1.5.19 long-memory thermal demand model."""

from __future__ import annotations

from typing import Any

from .coordinator_v1517 import CasaESEnergyCoordinator as V1517Coordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_BASE_TEMP_C,
    CONF_THERMAL_NORMAL_MAX_TEMP_C,
)
from .thermal_learning_v1519 import ThermalLearnerV1519

THERMAL_DRAW_WINDOW_DAYS = 30
THERMAL_DRAW_MARGIN_C = 2.0
THERMAL_ADAPTIVE_STORAGE_BUFFER_C = 2.0
THERMAL_DRAW_CAP_C = 10.0
THERMAL_RECENT_MIN_DAYS = 1


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V1517Coordinator):
    """v1.5.19 coordinator with 30-day weighted DHW demand memory."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self.thermal_learner = ThermalLearnerV1519(hass, entry.entry_id)

    def _thermal_target(
        self, item: dict[str, Any], data: dict[str, Any], now: Any
    ) -> tuple[float, str]:
        base = _number(item.get(CONF_THERMAL_BASE_TEMP_C), 53.0)
        normal_max = max(_number(item.get(CONF_THERMAL_NORMAL_MAX_TEMP_C), 65.0), base)
        subentry_id = str(item.get("subentry_id") or "")

        observed_days = self.thermal_learner.recent_draw_days(subentry_id)
        weighted_draw = self.thermal_learner.expected_draw_c_recent(
            subentry_id, now.hour, 24
        )

        source = "media ponderata 30 giorni"
        if observed_days < THERMAL_RECENT_MIN_DAYS:
            weighted_draw = self.thermal_learner.expected_draw_c(
                subentry_id, now.hour, 24
            )
            source = "bootstrap storico in attesa di giorni osservati"

        expected_draw = min(max(weighted_draw, 0.0), THERMAL_DRAW_CAP_C)
        learned_target = base + expected_draw + THERMAL_DRAW_MARGIN_C
        target = min(learned_target + THERMAL_ADAPTIVE_STORAGE_BUFFER_C, normal_max)
        target = max(target, base)

        reason = (
            f"base PDC {base:.1f}°C; {source}: prelievo previsto "
            f"{expected_draw:.1f}°C; margine comfort {THERMAL_DRAW_MARGIN_C:.1f}°C; "
            f"buffer accumulo {THERMAL_ADAPTIVE_STORAGE_BUFFER_C:.1f}°C; "
            f"massimo normale {normal_max:.1f}°C"
        )
        return round(target, 1), reason

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        diag = data.get("v1511_thermal_adaptive_target")
        if not isinstance(diag, dict):
            diag = {}
            data["v1511_thermal_adaptive_target"] = diag

        diag["window_days"] = THERMAL_DRAW_WINDOW_DAYS
        diag["model"] = "age_weighted_observed_days"
        diag["absence_policy"] = "missing_days_do_not_count_as_zero"
        diag["weighting"] = {
            "days_0_6": 1.0,
            "days_7_13": 0.75,
            "days_14_20": 0.50,
            "days_21_29": 0.30,
        }
        diag["margin_c"] = THERMAL_DRAW_MARGIN_C
        diag["adaptive_storage_buffer_c"] = THERMAL_ADAPTIVE_STORAGE_BUFFER_C

        for target in diag.get("targets") or []:
            subentry_id = str(target.get("subentry_id") or "")
            expected = round(
                self.thermal_learner.expected_draw_c_recent(
                    subentry_id, __import__("homeassistant.util.dt", fromlist=["now"]).now().hour, 24
                ),
                2,
            )
            days = self.thermal_learner.recent_draw_days(subentry_id)
            target["recent_30d_weighted_expected_draw_c"] = expected
            target["recent_30d_observed_days"] = days
            target["recent_7d_expected_draw_c"] = expected
            target["recent_draw_days"] = days

        data["v1519_thermal_long_memory"] = {
            "history_days": THERMAL_DRAW_WINDOW_DAYS,
            "model": "age_weighted_observed_days",
            "missing_days_count_as_zero": False,
            "weights": diag["weighting"],
        }
        return data

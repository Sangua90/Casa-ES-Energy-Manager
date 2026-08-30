"""Casa ES Energy Manager v1.5.11 adaptive DHW thermal target.

The native boiler/heat-pump remains responsible for the configured base
comfort temperature. Casa ES uses resistance Boost only above that base and
chooses the extra storage temperature from the last seven days of observed DHW
draws, with a small comfort margin. The normal configured maximum remains a
hard ceiling for ordinary surplus harvesting.
"""

from __future__ import annotations

from typing import Any

from .coordinator_v1510 import CasaESEnergyCoordinator as V1510Coordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_BASE_TEMP_C,
    CONF_THERMAL_NORMAL_MAX_TEMP_C,
)

THERMAL_DRAW_WINDOW_DAYS = 7
THERMAL_DRAW_MARGIN_C = 2.0
THERMAL_DRAW_CAP_C = 10.0
THERMAL_RECENT_MIN_DAYS = 1


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V1510Coordinator):
    """v1.5.11 coordinator with demand-based DHW surplus storage."""

    def _thermal_target(
        self, item: dict[str, Any], data: dict[str, Any], now: Any
    ) -> tuple[float, str]:
        base = _number(item.get(CONF_THERMAL_BASE_TEMP_C), 53.0)
        normal_max = max(_number(item.get(CONF_THERMAL_NORMAL_MAX_TEMP_C), 65.0), base)
        subentry_id = str(item.get("subentry_id") or "")

        recent_days = self.thermal_learner.recent_draw_days(subentry_id)
        recent_draw = self.thermal_learner.expected_draw_c_recent(
            subentry_id, now.hour, 24
        )

        # During the first learning day preserve useful historical knowledge as a
        # conservative bootstrap. As soon as a real recent day exists the rolling
        # seven-day household pattern becomes authoritative.
        source = "media mobile 7 giorni"
        if recent_days < THERMAL_RECENT_MIN_DAYS:
            recent_draw = self.thermal_learner.expected_draw_c(
                subentry_id, now.hour, 24
            )
            source = "bootstrap storico in attesa della finestra 7 giorni"

        expected_draw = min(max(recent_draw, 0.0), THERMAL_DRAW_CAP_C)
        target = min(base + expected_draw + THERMAL_DRAW_MARGIN_C, normal_max)
        target = max(target, base)

        reason = (
            f"base PDC {base:.1f}°C; {source}: prelievo previsto "
            f"{expected_draw:.1f}°C; margine {THERMAL_DRAW_MARGIN_C:.1f}°C; "
            f"massimo normale {normal_max:.1f}°C"
        )
        return round(target, 1), reason

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        targets: list[dict[str, Any]] = []
        now = __import__("homeassistant.util.dt", fromlist=["now"]).now()
        for raw in data.get("managed_device_configs") or []:
            if str(raw.get("device_type") or "") != "thermal_storage":
                continue
            item = self._thermal_context(dict(raw))
            target, reason = self._thermal_target(item, data, now)
            subentry_id = str(item.get("subentry_id") or "")
            targets.append(
                {
                    "subentry_id": subentry_id,
                    "name": item.get("name"),
                    "base_temperature_c": _number(item.get(CONF_THERMAL_BASE_TEMP_C), 53.0),
                    "adaptive_target_c": target,
                    "current_temperature_c": item.get("thermal_current_temperature_c"),
                    "normal_max_temperature_c": _number(item.get(CONF_THERMAL_NORMAL_MAX_TEMP_C), 65.0),
                    "recent_draw_days": self.thermal_learner.recent_draw_days(subentry_id),
                    "recent_7d_expected_draw_c": round(
                        self.thermal_learner.expected_draw_c_recent(subentry_id, now.hour, 24), 2
                    ),
                    "margin_c": THERMAL_DRAW_MARGIN_C,
                    "reason": reason,
                }
            )
        data["v1511_thermal_adaptive_target"] = {
            "window_days": THERMAL_DRAW_WINDOW_DAYS,
            "margin_c": THERMAL_DRAW_MARGIN_C,
            "base_owned_by_native_heat_pump": True,
            "resistance_boost_only_above_base": True,
            "normal_max_is_ceiling": True,
            "targets": targets,
        }
        return data

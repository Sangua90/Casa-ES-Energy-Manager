"""Casa ES Energy Manager v1.5.1 coordinator fixes.

Fixes climate anti-cycling so Home Assistant metadata/reloads never create a
fake compressor lockout. The timer is based only on real active/inactive state
transitions observed by Casa ES. It also migrates the legacy 20/20 climate
anti-cycle profile to 20 min ON / 5 min OFF at runtime.
"""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_MIN_OFF_MINUTES,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_CLIMATE,
)
from .coordinator_v15 import CasaESEnergyCoordinator as V15Coordinator
from .device_dry_run import _state_active


class CasaESEnergyCoordinator(V15Coordinator):
    """v1.5.1 controller with transition-based climate anti-cycling."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._observed_entity_active: dict[str, bool] = {}
        self._last_real_transition_at: dict[str, Any] = {}

    def _managed_device_snapshots(self) -> list[dict[str, Any]]:
        devices = super()._managed_device_snapshots()
        now = dt_util.utcnow()

        for item in devices:
            subentry_id = str(item.get("subentry_id") or "")
            if not subentry_id:
                item["seconds_since_change"] = None
                continue

            active = _state_active(item.get("state"))
            previous = self._observed_entity_active.get(subentry_id)

            # First observation after integration start/reload establishes the
            # baseline only. It must never invent a recent OFF/ON event.
            if previous is None:
                self._observed_entity_active[subentry_id] = active
                item["seconds_since_change"] = None
            elif previous != active:
                self._observed_entity_active[subentry_id] = active
                self._last_real_transition_at[subentry_id] = now
                item["seconds_since_change"] = 0.0
            else:
                changed_at = self._last_real_transition_at.get(subentry_id)
                item["seconds_since_change"] = (
                    max((now - changed_at).total_seconds(), 0.0)
                    if changed_at is not None
                    else None
                )

            # v1.5 used 20/20 on the installed climate profiles. Preserve the
            # useful 20-minute minimum ON period but shorten only that exact
            # legacy pair to a 5-minute compressor restart guard.
            if str(item.get(CONF_DEVICE_TYPE) or "") == DEVICE_TYPE_CLIMATE:
                try:
                    min_on = float(item.get(CONF_DEVICE_MIN_ON_MINUTES) or 0.0)
                    min_off = float(item.get(CONF_DEVICE_MIN_OFF_MINUTES) or 0.0)
                except (TypeError, ValueError):
                    min_on = min_off = 0.0
                if abs(min_on - 20.0) < 1e-9 and abs(min_off - 20.0) < 1e-9:
                    item[CONF_DEVICE_MIN_OFF_MINUTES] = 5.0
                    item["anti_cycle_profile_migrated_v151"] = True

            item["anti_cycle_transition_source"] = "real_observed_transition"

        return devices

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v151_transition_based_anti_cycle"] = True
        data["v151_climate_legacy_20_20_effective_profile"] = "20_on_5_off"
        data["v151_independent_safety_margins"] = {
            "inverter_w": float(data.get("inverter_safety_margin_w") or 250.0),
            "phase_w": float(data.get("phase_safety_margin_w") or 150.0),
            "grid_w": float(data.get("grid_safety_margin_w") or 300.0),
        }
        return data

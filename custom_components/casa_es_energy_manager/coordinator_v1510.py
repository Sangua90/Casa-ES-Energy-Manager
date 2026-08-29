"""Casa ES Energy Manager v1.5.10 climate/thermal corrective layer.

Fixes live issues observed in diagnostics:
- phase-only warnings never become immediate managed-load hard stops;
- max daily activations blocks only the next start, never stops an active climate;
- thermal storage below base temperature cannot remain idle indefinitely when
  the appliance is not heating and sufficient PV is available;
- configured climate minimum-OFF always wins over the legacy v1.5.1 runtime 20/5 migration.
Contaminated climate activation counters are reset once per day/version marker.
"""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_MIN_BATTERY_SOC,
    CONF_DEVICE_MIN_OFF_MINUTES,
    CONF_DEVICE_NOMINAL_POWER_W,
    CONF_DEVICE_TYPE,
    DEVICE_MODE_AUTO,
    DEVICE_TYPE_CLIMATE,
)
from .coordinator_v158 import CasaESEnergyCoordinator as V158Coordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_BASE_TEMP_C,
    CONF_THERMAL_BOOST_ENTITY,
    DEVICE_TYPE_THERMAL,
)

RESET_MARKER_KEY = "v1510_climate_activation_reset_date"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V158Coordinator):
    """v1.5.10 corrective layer for climate stability and DHW recovery."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._v1510_activation_reset_date: str | None = None

    async def async_initialize(self) -> None:
        await super().async_initialize()
        today = dt_util.now().date().isoformat()
        stored = await self._runtime_store.async_load()
        marker = stored.get(RESET_MARKER_KEY) if isinstance(stored, dict) else None
        if marker == today:
            self._v1510_activation_reset_date = today
            return

        climate_ids = {
            str(subentry.subentry_id)
            for subentry in self.entry.subentries.values()
            if str(subentry.data.get(CONF_DEVICE_TYPE) or "") == DEVICE_TYPE_CLIMATE
        }
        for subentry_id in climate_ids:
            self._runtime_activations[subentry_id] = 0
        self._v1510_activation_reset_date = today
        await self._async_save_runtime_state()

    async def _async_save_runtime_state(self) -> None:
        await super()._async_save_runtime_state()
        if not self._v1510_activation_reset_date:
            return
        stored = await self._runtime_store.async_load()
        if not isinstance(stored, dict):
            return
        if stored.get(RESET_MARKER_KEY) == self._v1510_activation_reset_date:
            return
        stored[RESET_MARKER_KEY] = self._v1510_activation_reset_date
        await self._runtime_store.async_save(stored)

    def _managed_device_snapshots(self) -> list[dict[str, Any]]:
        devices = super()._managed_device_snapshots()
        configured = {
            str(subentry.subentry_id): subentry.data
            for subentry in self.entry.subentries.values()
        }
        for item in devices:
            if str(item.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_CLIMATE:
                continue
            source = configured.get(str(item.get("subentry_id") or "")) or {}
            if source.get(CONF_DEVICE_MIN_OFF_MINUTES) not in (None, ""):
                item[CONF_DEVICE_MIN_OFF_MINUTES] = max(
                    _number(source.get(CONF_DEVICE_MIN_OFF_MINUTES), 20.0), 0.0
                )
                item["anti_cycle_profile_migrated_v151"] = False
                item["anti_cycle_profile_source"] = "configured_v1510"
        return devices

    @staticmethod
    def _normalize_stop_decisions(data: dict[str, Any]) -> None:
        configs = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("managed_device_configs") or [])
        }
        phase_only = bool(
            data.get("phase_warning")
            and not data.get("inverter_warning")
            and not data.get("grid_warning")
        )

        for decision in data.get("dry_run_decisions") or []:
            if not decision.get("entity_active"):
                continue
            source = configs.get(str(decision.get("subentry_id") or "")) or {}
            is_climate = str(source.get(CONF_DEVICE_TYPE) or "") == DEVICE_TYPE_CLIMATE
            reason = str(decision.get("reason") or "")

            # v1.5.6 design: phase-only warning is diagnostic/advisory for every
            # managed load. Hard action belongs to inverter/grid protection.
            if phase_only and decision.get("stop_is_hard_safety"):
                decision["would_stop"] = False
                decision["stop_is_hard_safety"] = False
                decision["decision"] = "phase_warning_advisory"
                decision["reason"] = (
                    "Avviso carico fase: solo diagnostica, nessuno spegnimento automatico."
                )
                continue

            # A daily activation cap is a start-admission rule, not a running-stop rule.
            if is_climate and "Numero massimo di avvii giornalieri raggiunto" in reason:
                decision["would_stop"] = False
                decision["stop_is_hard_safety"] = False
                decision["decision"] = "daily_activation_limit_running"
                decision["reason"] = (
                    "Limite avvii raggiunto: il climatizzatore già acceso resta attivo; "
                    "Casa ES blocca soltanto un nuovo avvio automatico."
                )

    async def _async_apply_thermal_control(
        self, data: dict[str, Any], now: Any
    ) -> bool:
        """Recover an idle boiler below base temp when PV can safely cover it."""
        if self.real_control_enabled:
            allocation = data.get("v156_battery_allocation") or self._battery_allocation(data)
            overflow = max(_number(allocation.get("overflow_w"), 0.0), 0.0)
            below_target = bool(allocation.get("below_target"))

            for raw in data.get("managed_device_configs") or []:
                if str(raw.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_THERMAL:
                    continue
                item = self._thermal_context(dict(raw))
                if str(item.get("management_mode") or DEVICE_MODE_AUTO) != DEVICE_MODE_AUTO:
                    continue
                if item.get("thermal_legionella_active"):
                    continue
                if item.get("thermal_boost_active") and not item.get(
                    "thermal_boost_owned_by_casa_es"
                ):
                    continue
                if item.get("thermal_heating"):
                    continue

                temp = item.get("thermal_current_temperature_c")
                if temp is None:
                    continue
                base = _number(item.get(CONF_THERMAL_BASE_TEMP_C), 52.0)
                if _number(temp) >= base - 0.5:
                    continue
                if _number(data.get("battery_soc"), 0.0) < _number(
                    item.get(CONF_DEVICE_MIN_BATTERY_SOC), 0.0
                ):
                    continue

                nominal = max(_number(item.get(CONF_DEVICE_NOMINAL_POWER_W), 0.0), 1.0)
                available = (
                    overflow
                    if below_target
                    else max(
                        _number(data.get("solar_after_house_w"), 0.0),
                        _number(data.get("pv_potential_after_house_w"), 0.0),
                    )
                )
                if available < nominal * 0.9:
                    continue

                boost_entity = str(item.get(CONF_THERMAL_BOOST_ENTITY) or "")
                entity_id = str(item.get("entity_id") or "")
                subentry_id = str(item.get("subentry_id") or "")
                if not boost_entity or not entity_id or not subentry_id:
                    continue

                target, target_reason = self._thermal_target(item, data, now)
                target = max(target, base)
                await self._set_water_temperature(entity_id, target)
                await self._set_boost(boost_entity, True)
                self._thermal_boost_owned.add(subentry_id)
                self._thermal_target_c[subentry_id] = target
                self._last_thermal_action = "boost_on_below_base_recovery"
                self._last_thermal_reason = (
                    f"Boiler fermo a {_number(temp):.1f}°C sotto base {base:.1f}°C; "
                    f"FV disponibile {available:.0f} W. Target {target:.1f}°C: {target_reason}"
                )
                self._last_thermal_at = now.isoformat()
                return True

        return await super()._async_apply_thermal_control(data, now)

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        self._normalize_stop_decisions(data)
        await super()._async_apply_real_control(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        self._normalize_stop_decisions(data)
        data["v1510_climate_min_off_uses_configured_value"] = True
        data["v1510_phase_only_warning_advisory"] = True
        data["v1510_daily_activation_limit_stops_running"] = False
        data["v1510_thermal_below_base_recovery"] = True
        data["v1510_activation_counter_reset_date"] = self._v1510_activation_reset_date
        return data

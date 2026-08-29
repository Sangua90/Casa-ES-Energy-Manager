"""Casa ES Energy Manager v1.5.10 climate/thermal corrective layer.

Fixes three issues observed in live diagnostics:
- phase-only warnings must never become immediate managed-load hard stops;
- max daily activations blocks the next start but never stops an active climate;
- thermal storage below base temperature must not remain idle indefinitely when
  the appliance is not heating and sufficient PV is available.
Also restores the configured climate minimum-OFF value after legacy v1.5.1
runtime migration logic and resets contaminated climate activation counts once.
"""

from __future__ import annotations

from typing import Any

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


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class CasaESEnergyCoordinator(V158Coordinator):
    """v1.5.10 final corrective layer for climate stability and DHW recovery."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._v1510_activation_reset_done = False

    async def async_initialize(self) -> None:
        await super().async_initialize()
        # Counts collected before v1.5.10 include cycles caused by the phase-stop
        # and 20/5 bugs. Reset only climate counters once for a clean baseline.
        if not self._v1510_activation_reset_done:
            climate_ids = {
                str(subentry.subentry_id)
                for subentry in self.entry.subentries.values()
                if str(subentry.data.get(CONF_DEVICE_TYPE) or "") == DEVICE_TYPE_CLIMATE
            }
            for subentry_id in climate_ids:
                self._runtime_activations[subentry_id] = 0
            self._v1510_activation_reset_done = True

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
    def _normalize_climate_stop_decisions(data: dict[str, Any]) -> None:
        phase_only = bool(
            data.get("phase_warning")
            and not data.get("inverter_warning")
            and not data.get("grid_warning")
        )
        for decision in data.get("dry_run_decisions") or []:
            if str(decision.get("device_type") or "") not in {"", DEVICE_TYPE_CLIMATE}:
                continue
            if not decision.get("entity_active"):
                continue

            reason = str(decision.get("reason") or "")
            if phase_only and decision.get("stop_is_hard_safety"):
                decision["would_stop"] = False
                decision["stop_is_hard_safety"] = False
                decision["decision"] = "phase_warning_advisory"
                decision["reason"] = (
                    "Avviso carico fase: solo diagnostica, nessuno spegnimento automatico."
                )

            if "Numero massimo di avvii giornalieri raggiunto" in reason:
                decision["would_stop"] = False
                decision["stop_is_hard_safety"] = False
                decision["decision"] = "daily_activation_limit_running"
                decision["reason"] = (
                    "Limite avvii raggiunto: il dispositivo già acceso resta attivo; "
                    "Casa ES bloccherà soltanto un nuovo avvio automatico."
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
                if below_target:
                    available = overflow
                else:
                    available = max(
                        _number(data.get("solar_after_house_w"), 0.0),
                        _number(data.get("pv_potential_after_house_w"), 0.0),
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
        self._normalize_climate_stop_decisions(data)
        await super()._async_apply_real_control(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        self._normalize_climate_stop_decisions(data)
        data["v1510_climate_min_off_uses_configured_value"] = True
        data["v1510_phase_only_warning_advisory"] = True
        data["v1510_daily_activation_limit_stops_running"] = False
        data["v1510_thermal_below_base_recovery"] = True
        data["v1510_activation_counter_reset"] = self._v1510_activation_reset_done
        return data

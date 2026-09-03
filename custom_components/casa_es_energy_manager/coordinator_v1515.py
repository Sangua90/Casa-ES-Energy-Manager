"""Casa ES Energy Manager v1.5.15 thermal recovery hardening."""

from __future__ import annotations

from typing import Any

from .const import CONF_DEVICE_ENABLED, CONF_DEVICE_ENTITY, CONF_DEVICE_TYPE, DEVICE_MODE_AUTO
from .coordinator_v1513 import CasaESEnergyCoordinator as V1513Coordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_BASE_TEMP_C,
    CONF_THERMAL_BOOST_ENTITY,
    CONF_THERMAL_LEGIONELLA_ENTITY,
    DEVICE_TYPE_THERMAL,
)
from .thermal_learning_v1515 import ThermalLearnerV1515

THERMAL_STALLED_BOOST_GRACE_SECONDS = 120.0
THERMAL_STALLED_BOOST_MAX_RETRIES = 2
THERMAL_ACTIVE_POWER_W = 20.0


class CasaESEnergyCoordinator(V1513Coordinator):
    """v1.5.15 coordinator with persistent learning and stalled-Boost recovery."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        # Replace the learner before async_initialize() loads persisted data.
        self.thermal_learner = ThermalLearnerV1515(hass, entry.entry_id)
        self._thermal_stalled_since: dict[str, Any] = {}
        self._thermal_stalled_retries: dict[str, int] = {}
        self._thermal_main_entity_commands_blocked = 0

    def _clear_stalled_thermal(self, subentry_id: str) -> None:
        self._thermal_stalled_since.pop(subentry_id, None)
        self._thermal_stalled_retries.pop(subentry_id, None)

    def _configured_thermal_main_entities(self) -> set[str]:
        """Return water-heater entities that Casa ES must never turn on/off."""
        entities: set[str] = set()
        for subentry in self.entry.subentries.values():
            data = subentry.data
            if str(data.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_THERMAL:
                continue
            entity_id = str(data.get(CONF_DEVICE_ENTITY) or "")
            if entity_id:
                entities.add(entity_id)
        return entities

    async def _async_call_entity_control(self, entity_id: str, turn_on: bool) -> None:
        """Hard firewall: thermal main entity is never managed as an on/off load.

        The Ariston must stay continuously available in its native heat-pump mode.
        Casa ES is allowed to manage only the dedicated Boost path (plus the
        temperature setpoint needed for that Boost), never water_heater turn_on/
        turn_off through generic managed-load control.
        """
        if entity_id in self._configured_thermal_main_entities():
            self._thermal_main_entity_commands_blocked += 1
            self._last_thermal_action = "main_entity_on_off_blocked"
            self._last_thermal_reason = (
                f"Comando generico {'ON' if turn_on else 'OFF'} bloccato su {entity_id}: "
                "il boiler resta sempre sotto controllo nativo; Casa ES gestisce solo Boost."
            )
            return
        await super()._async_call_entity_control(entity_id, turn_on)

    async def _async_apply_thermal_control(self, data: dict[str, Any], now: Any) -> bool:
        """Recover a Casa ES-owned Boost that is ON in HA but not really heating."""
        for raw in data.get("managed_device_configs") or []:
            if str(raw.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_THERMAL:
                continue
            item = self._thermal_context(dict(raw))
            if not bool(item.get(CONF_DEVICE_ENABLED, True)):
                continue
            if str(item.get("management_mode") or DEVICE_MODE_AUTO) != DEVICE_MODE_AUTO:
                continue
            if item.get("thermal_legionella_active"):
                continue

            subentry_id = str(item.get("subentry_id") or "")
            if not subentry_id or subentry_id not in self._thermal_boost_owned:
                self._clear_stalled_thermal(subentry_id)
                continue
            if not item.get("thermal_boost_active"):
                self._clear_stalled_thermal(subentry_id)
                continue

            heating = bool(item.get("thermal_heating"))
            try:
                power_w = float(item.get("current_power_w") or 0.0)
            except (TypeError, ValueError):
                power_w = 0.0

            if heating or power_w > THERMAL_ACTIVE_POWER_W:
                self._clear_stalled_thermal(subentry_id)
                continue

            since = self._thermal_stalled_since.get(subentry_id)
            if since is None:
                self._thermal_stalled_since[subentry_id] = now
                continue
            elapsed = (now - since).total_seconds()
            if elapsed < THERMAL_STALLED_BOOST_GRACE_SECONDS:
                continue

            retries = int(self._thermal_stalled_retries.get(subentry_id, 0))
            if retries >= THERMAL_STALLED_BOOST_MAX_RETRIES:
                # Do not leave the boiler indefinitely in a fake BOOST state.
                # Release Casa ES ownership and restore the native heat-pump base
                # so the Ariston can at least recover domestic hot water normally.
                await self._stop_owned_thermal_boost(
                    item,
                    "Boost Ariston non ha avviato il riscaldamento: fallback alla PDC nativa",
                    now,
                )
                self._clear_stalled_thermal(subentry_id)
                return True

            target = float(
                self._thermal_target_c.get(
                    subentry_id,
                    item.get("thermal_target_temperature_c")
                    or item.get(CONF_THERMAL_BASE_TEMP_C)
                    or 53.0,
                )
            )
            boost_entity = str(item.get(CONF_THERMAL_BOOST_ENTITY) or "")
            entity_id = str(item.get(CONF_DEVICE_ENTITY) or item.get("entity_id") or "")
            if not boost_entity or not entity_id:
                continue

            # Re-arm only the dedicated Boost path. This never uses generic
            # water_heater turn_on/turn_off on the main Ariston entity.
            await self._set_boost(boost_entity, False)
            await self._set_water_temperature(entity_id, target)
            await self._set_boost(boost_entity, True)
            self._thermal_stalled_retries[subentry_id] = retries + 1
            self._thermal_stalled_since[subentry_id] = now
            self._last_thermal_action = "boost_retrigger"
            self._last_thermal_reason = (
                f"Boost Ariston inattivo per {elapsed:.0f}s: ritentativo "
                f"{retries + 1}/{THERMAL_STALLED_BOOST_MAX_RETRIES} verso {target:.1f}°C"
            )
            self._last_thermal_at = now.isoformat()
            return True

        return await super()._async_apply_thermal_control(data, now)

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v1515_thermal_main_entity_policy"] = {
            "main_entity_always_native": True,
            "generic_on_off_blocked": True,
            "casa_es_controls_boost_only": True,
            "blocked_generic_commands": self._thermal_main_entity_commands_blocked,
            "thermal_main_entities": sorted(self._configured_thermal_main_entities()),
        }
        return data

"""Casa ES Energy Manager v1.5.13 verified multi-split climate groups."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_DEVICE_CLIMATE_GROUP_ENTITIES,
    CONF_DEVICE_ENTITY,
    CONF_DEVICE_TYPE,
    DEVICE_MODE_AUTO,
    DEVICE_TYPE_CLIMATE,
)
from .coordinator_v1512 import CasaESEnergyCoordinator as V1512Coordinator


class CasaESEnergyCoordinator(V1512Coordinator):
    """Control configured climate entities as one verified group in Auto mode."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._v1513_group_last_target: dict[str, bool] = {}
        self._v1513_group_last_result: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _climate_state_active(state: Any) -> bool:
        return bool(
            state is not None
            and state.state not in (STATE_OFF, STATE_UNKNOWN, STATE_UNAVAILABLE)
        )

    def _configured_climate_groups(self) -> dict[str, tuple[str, list[str]]]:
        groups: dict[str, tuple[str, list[str]]] = {}
        for subentry in self.entry.subentries.values():
            data = subentry.data
            if str(data.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_CLIMATE:
                continue
            primary = str(data.get(CONF_DEVICE_ENTITY) or "")
            raw = data.get(CONF_DEVICE_CLIMATE_GROUP_ENTITIES) or []
            if isinstance(raw, str):
                raw = [raw]
            entities: list[str] = []
            for entity_id in [primary, *list(raw)]:
                entity_id = str(entity_id or "")
                if entity_id.startswith("climate.") and entity_id not in entities:
                    entities.append(entity_id)
            if len(entities) >= 2 and primary:
                groups[primary] = (str(subentry.subentry_id), entities)
        return groups

    async def _async_call_group(self, entities: list[str], turn_on: bool) -> dict[str, Any]:
        service = "turn_on" if turn_on else "turn_off"
        errors: dict[str, str] = {}
        for entity_id in entities:
            try:
                await super()._async_call_entity_control(entity_id, turn_on)
            except Exception as err:
                errors[entity_id] = str(err)

        # Service calls are blocking, but integrations may publish state just after
        # returning. Re-read the actual HA states and retry only mismatching members.
        mismatched: list[str] = []
        for entity_id in entities:
            active = self._climate_state_active(self.hass.states.get(entity_id))
            if active != turn_on:
                mismatched.append(entity_id)
        for entity_id in list(mismatched):
            try:
                await super()._async_call_entity_control(entity_id, turn_on)
            except Exception as err:
                errors[entity_id] = str(err)

        states = {
            entity_id: (
                self.hass.states.get(entity_id).state
                if self.hass.states.get(entity_id) is not None
                else "missing"
            )
            for entity_id in entities
        }
        verified = all(
            self._climate_state_active(self.hass.states.get(entity_id)) == turn_on
            for entity_id in entities
        )
        return {
            "target": service,
            "verified": verified,
            "states": states,
            "errors": errors,
        }

    async def _async_call_entity_control(self, entity_id: str, turn_on: bool) -> None:
        group = self._configured_climate_groups().get(entity_id)
        if group is None:
            await super()._async_call_entity_control(entity_id, turn_on)
            return

        subentry_id, entities = group
        # Generic real control reaches this method only for an Auto decision.
        # Override/manual mode therefore remains completely free for the user.
        if self.device_modes.get(subentry_id, DEVICE_MODE_AUTO) != DEVICE_MODE_AUTO:
            return
        result = await self._async_call_group(entities, turn_on)
        self._v1513_group_last_target[subentry_id] = turn_on
        self._v1513_group_last_result[subentry_id] = result
        if not result["verified"]:
            raise HomeAssistantError(
                "Gruppo clima non sincronizzato dopo il comando: "
                + ", ".join(f"{entity}={state}" for entity, state in result["states"].items())
            )

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        diagnostics: list[dict[str, Any]] = []
        for primary, (subentry_id, entities) in self._configured_climate_groups().items():
            states = {
                entity_id: (
                    self.hass.states.get(entity_id).state
                    if self.hass.states.get(entity_id) is not None
                    else "missing"
                )
                for entity_id in entities
            }
            active = [
                entity_id
                for entity_id in entities
                if self._climate_state_active(self.hass.states.get(entity_id))
            ]
            diagnostics.append(
                {
                    "subentry_id": subentry_id,
                    "primary_entity": primary,
                    "entities": entities,
                    "states": states,
                    "active_count": len(active),
                    "total_count": len(entities),
                    "all_on": len(active) == len(entities),
                    "all_off": len(active) == 0,
                    "synchronized": len(active) in (0, len(entities)),
                    "management_mode": self.device_modes.get(subentry_id, DEVICE_MODE_AUTO),
                    "last_auto_target_on": self._v1513_group_last_target.get(subentry_id),
                    "last_command_result": self._v1513_group_last_result.get(subentry_id),
                }
            )
        data["v1513_climate_groups"] = {
            "manual_mode_independent": True,
            "auto_mode_group_control": True,
            "verify_all_members": True,
            "retry_mismatching_members_once": True,
            "groups": diagnostics,
        }
        return data

"""Switch platform for guarded Casa ES real appliance control."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_AUTOMATIC_REAL_LOAD_CONTROL,
    DEFAULT_AUTOMATIC_REAL_LOAD_CONTROL,
    DOMAIN,
    NAME,
    VERSION,
)
from .coordinator_v1 import CasaESEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CasaESRealControlSwitch(hass, entry, coordinator)])


class CasaESRealControlSwitch(SwitchEntity):
    """Explicit opt-in master switch for physical appliance commands."""

    _attr_has_entity_name = True
    _attr_name = "Controllo automatico reale"
    _attr_icon = "mdi:robot-industrial"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: CasaESEnergyCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_automatic_real_load_control"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Casa ES",
            model="Energy Manager",
            sw_version=VERSION,
        )

    @property
    def is_on(self) -> bool:
        return bool(
            self.entry.options.get(
                CONF_AUTOMATIC_REAL_LOAD_CONTROL,
                self.entry.data.get(
                    CONF_AUTOMATIC_REAL_LOAD_CONTROL,
                    DEFAULT_AUTOMATIC_REAL_LOAD_CONTROL,
                ),
            )
        )

    async def _async_set(self, enabled: bool) -> None:
        options = dict(self.entry.options)
        options[CONF_AUTOMATIC_REAL_LOAD_CONTROL] = enabled
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(False)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "safety": "Le protezioni elettriche deterministiche hanno sempre precedenza.",
            "manual_mode": "I dispositivi in Manuale non vengono mai comandati da Casa ES.",
        }

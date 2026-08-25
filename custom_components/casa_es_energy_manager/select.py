"""Select platform for per-device Casa ES runtime modes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_DEVICE_NAME,
    DEVICE_MODE_AUTO,
    DEVICE_MODE_OFF,
    DEVICE_MODE_OVERRIDE,
    DOMAIN,
    NAME,
    SUBENTRY_TYPE_MANAGED_DEVICE,
    VERSION,
)
from .coordinator import CasaESEnergyCoordinator

DISPLAY_TO_MODE = {
    "AUTO": DEVICE_MODE_AUTO,
    "OVERRIDE": DEVICE_MODE_OVERRIDE,
    "OFF": DEVICE_MODE_OFF,
}
MODE_TO_DISPLAY = {value: key for key, value in DISPLAY_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one runtime mode selector for each managed device."""
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[CasaESManagedDeviceModeSelect] = []
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MANAGED_DEVICE:
            continue
        name = str(subentry.data.get(CONF_DEVICE_NAME) or subentry.title)
        entities.append(
            CasaESManagedDeviceModeSelect(
                coordinator=coordinator,
                entry=entry,
                subentry_id=subentry.subentry_id,
                device_name=name,
            )
        )
    async_add_entities(entities)


class CasaESManagedDeviceModeSelect(RestoreEntity, SelectEntity):
    """AUTO / OVERRIDE / OFF selector for one managed appliance."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:toggle-switch"
    _attr_options = ["AUTO", "OVERRIDE", "OFF"]

    def __init__(
        self,
        *,
        coordinator: CasaESEnergyCoordinator,
        entry: ConfigEntry,
        subentry_id: str,
        device_name: str,
    ) -> None:
        self.coordinator = coordinator
        self.subentry_id = subentry_id
        self.device_name = device_name
        self._attr_unique_id = f"{entry.entry_id}_{subentry_id}_management_mode"
        self._attr_name = f"Modalità gestione {device_name}"
        self._attr_current_option = "AUTO"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Casa ES",
            model="Energy Manager",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last manual mode after a restart."""
        await super().async_added_to_hass()
        restored = await self.async_get_last_state()
        option = restored.state if restored and restored.state in self.options else "AUTO"
        self._attr_current_option = option
        self.coordinator.set_device_mode(
            self.subentry_id, DISPLAY_TO_MODE.get(option, DEVICE_MODE_AUTO)
        )
        await self.coordinator.async_request_refresh()

    async def async_select_option(self, option: str) -> None:
        """Change only this managed device's runtime mode."""
        if option not in self.options:
            raise ValueError(f"Unsupported Casa ES device mode: {option}")
        self._attr_current_option = option
        self.coordinator.set_device_mode(self.subentry_id, DISPLAY_TO_MODE[option])
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

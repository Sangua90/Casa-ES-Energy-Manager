"""Button platform for Casa ES Energy Manager."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION
from .coordinator import CasaESEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Casa ES buttons."""
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CasaESRefreshAIButton(coordinator, entry)])


class CasaESRefreshAIButton(CoordinatorEntity[CasaESEnergyCoordinator], ButtonEntity):
    """Force an advisory AI planner refresh."""

    _attr_has_entity_name = True
    _attr_name = "Aggiorna strategia AI"
    _attr_icon = "mdi:brain"

    def __init__(
        self,
        coordinator: CasaESEnergyCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh_ai_strategy"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Casa ES",
            model="Energy Manager",
            sw_version=VERSION,
        )

    async def async_press(self) -> None:
        """Request a fresh AI recommendation."""
        if self.coordinator.ai_planner is not None:
            await self.coordinator.ai_planner.async_refresh()

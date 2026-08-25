"""Button entities for Casa ES Energy Manager v1."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION
from .coordinator_v1 import CasaESEnergyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CasaESRefreshAIButton(coordinator, entry),
            CasaESEmergencyChargeStartButton(coordinator, entry),
            CasaESEmergencyChargeStopButton(coordinator, entry),
        ]
    )


class _BaseButton(CoordinatorEntity[CasaESEnergyCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CasaESEnergyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Casa ES",
            model="Energy Manager",
            sw_version=VERSION,
        )


class CasaESRefreshAIButton(_BaseButton):
    _attr_name = "Aggiorna strategia AI"
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator: CasaESEnergyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_refresh_ai_strategy"

    async def async_press(self) -> None:
        if self.coordinator.ai_planner is not None:
            await self.coordinator.ai_planner.async_refresh()


class CasaESEmergencyChargeStartButton(_BaseButton):
    _attr_name = "Avvia ricarica di emergenza batteria"
    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, coordinator: CasaESEnergyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_emergency_charge_start"

    @property
    def available(self) -> bool:
        return self.coordinator.emergency_charge_available and not self.coordinator.emergency_charge_active

    async def async_press(self) -> None:
        await self.coordinator.async_start_emergency_charge()


class CasaESEmergencyChargeStopButton(_BaseButton):
    _attr_name = "Interrompi ricarica di emergenza batteria"
    _attr_icon = "mdi:battery-off-outline"

    def __init__(self, coordinator: CasaESEnergyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_emergency_charge_stop"

    @property
    def available(self) -> bool:
        return self.coordinator.emergency_charge_available and self.coordinator.emergency_charge_active

    async def async_press(self) -> None:
        await self.coordinator.async_stop_emergency_charge("manual")

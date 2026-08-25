"""Select platform for Casa ES day-to-day controls."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_DEVICE_NAME,
    CONF_ENERGY_PREFERENCE,
    DEFAULT_ENERGY_PREFERENCE,
    DEVICE_MODE_AUTO,
    DEVICE_MODE_OFF,
    DEVICE_MODE_OVERRIDE,
    DOMAIN,
    ENERGY_PREFERENCE_BALANCED,
    ENERGY_PREFERENCE_BATTERY,
    ENERGY_PREFERENCE_LOADS,
    NAME,
    SUBENTRY_TYPE_MANAGED_DEVICE,
    VERSION,
)
from .coordinator_v1 import CasaESEnergyCoordinator

DEVICE_DISPLAY_TO_MODE = {
    "Automatico": DEVICE_MODE_AUTO,
    "Manuale": DEVICE_MODE_OVERRIDE,
    "Spento": DEVICE_MODE_OFF,
}
MODE_TO_DEVICE_DISPLAY = {value: key for key, value in DEVICE_DISPLAY_TO_MODE.items()}
LEGACY_DEVICE_DISPLAY = {
    "AUTO": "Automatico",
    "OVERRIDE": "Manuale",
    "OFF": "Spento",
}

PREFERENCE_DISPLAY_TO_VALUE = {
    "Batteria prioritaria": ENERGY_PREFERENCE_BATTERY,
    "Bilanciata": ENERGY_PREFERENCE_BALANCED,
    "Carichi prioritari": ENERGY_PREFERENCE_LOADS,
}
VALUE_TO_PREFERENCE_DISPLAY = {
    value: key for key, value in PREFERENCE_DISPLAY_TO_VALUE.items()
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = [
        CasaESEnergyPreferenceSelect(
            coordinator=coordinator,
            entry=entry,
            hass=hass,
        )
    ]
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


class _CasaESSelectBase(SelectEntity):
    _attr_has_entity_name = True

    def _set_device_info(self, entry: ConfigEntry) -> None:
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Casa ES",
            model="Energy Manager",
            sw_version=VERSION,
        )


class CasaESEnergyPreferenceSelect(_CasaESSelectBase):
    """Change the global battery/load preference from the dashboard."""

    _attr_icon = "mdi:tune-variant"
    _attr_name = "Strategia energetica"
    _attr_options = list(PREFERENCE_DISPLAY_TO_VALUE)

    def __init__(
        self,
        *,
        coordinator: CasaESEnergyCoordinator,
        entry: ConfigEntry,
        hass: HomeAssistant,
    ) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self.hass = hass
        self._attr_unique_id = f"{entry.entry_id}_energy_preference"
        configured = entry.options.get(
            CONF_ENERGY_PREFERENCE,
            entry.data.get(CONF_ENERGY_PREFERENCE, DEFAULT_ENERGY_PREFERENCE),
        )
        self._attr_current_option = VALUE_TO_PREFERENCE_DISPLAY.get(
            str(configured), "Bilanciata"
        )
        self._set_device_info(entry)

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unsupported Casa ES energy preference: {option}")
        value = PREFERENCE_DISPLAY_TO_VALUE[option]
        new_options = dict(self.entry.options)
        new_options[CONF_ENERGY_PREFERENCE] = value
        self.hass.config_entries.async_update_entry(self.entry, options=new_options)
        self._attr_current_option = option
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()


class CasaESManagedDeviceModeSelect(RestoreEntity, _CasaESSelectBase):
    """Automatico / Manuale / Spento selector for one managed appliance."""

    _attr_icon = "mdi:toggle-switch"
    _attr_options = list(DEVICE_DISPLAY_TO_MODE)

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
        self._attr_unique_id = f"{entry.entry_id}_{subentry_id}_management_mode"
        self._attr_name = f"Modalità {device_name}"
        self._attr_current_option = "Automatico"
        self._set_device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        restored = await self.async_get_last_state()
        option = restored.state if restored else "Automatico"
        option = LEGACY_DEVICE_DISPLAY.get(option, option)
        if option not in self.options:
            option = "Automatico"
        self._attr_current_option = option
        self.coordinator.set_device_mode(
            self.subentry_id,
            DEVICE_DISPLAY_TO_MODE.get(option, DEVICE_MODE_AUTO),
        )
        await self.coordinator.async_request_refresh()

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError(f"Unsupported Casa ES device mode: {option}")
        self._attr_current_option = option
        self.coordinator.set_device_mode(
            self.subentry_id,
            DEVICE_DISPLAY_TO_MODE[option],
        )
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

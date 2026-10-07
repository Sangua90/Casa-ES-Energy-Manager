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
    if hasattr(coordinator, "house_gas_mode"):
        entities.append(CasaESHouseGasModeSelect(coordinator, entry))
        entities.append(CasaESHouseSituationSelect(coordinator, entry))
        for machine, name in (("salotto", "Clima Salotto"), ("ester", "Clima Ester"), ("p1", "Clima P1")):
            entities.append(CasaESHouseMachineModeSelect(coordinator, entry, machine, name))
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


class CasaESHouseGasModeSelect(_CasaESSelectBase):
    """Only the space-heating thermostat; never boiler main power or DHW."""
    _attr_name = "Riscaldamento caldaia"
    _attr_icon = "mdi:radiator"
    _attr_options = ["Automatico", "Spento"]

    def __init__(self, coordinator, entry):
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_house_gas_mode"
        self._set_device_info(entry)

    @property
    def current_option(self):
        return "Automatico" if self.coordinator.house_gas_mode == "auto" else "Spento"

    async def async_select_option(self, option):
        values = {"Automatico": "auto", "Spento": "off"}
        if option not in values:
            raise ValueError("Modalità caldaia non valida")
        await self.coordinator.async_set_house_gas_mode(values[option])
        self.async_write_ha_state()


class CasaESHouseMachineModeSelect(_CasaESSelectBase):
    _attr_icon = "mdi:air-conditioner"
    _attr_options = ["Automatico", "Manuale", "Spento"]

    def __init__(self, coordinator, entry, machine, name):
        self.coordinator, self.machine = coordinator, machine
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_house_machine_{machine}"
        self._set_device_info(entry)

    @property
    def current_option(self):
        return {"auto": "Automatico", "manual": "Manuale", "off": "Spento"}.get(self.coordinator.house_machine_modes.get(self.machine), "Manuale")

    async def async_select_option(self, option):
        values = {"Automatico": "auto", "Manuale": "manual", "Spento": "off"}
        if option not in values:
            raise ValueError("Modalità climatizzatore non valida")
        await self.coordinator.async_set_house_machine_mode(self.machine, values[option])
        self.async_write_ha_state()


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


class CasaESHouseSituationSelect(_CasaESSelectBase):
    """Time-limited house overrides; routines resume at explicit expiry."""
    _attr_name = "Situazione casa"
    _attr_icon = "mdi:home-clock"
    _attr_options = ["Normale", "Qualcuno a casa (12 ore)", "Assenza breve (4 ore)", "Weekend fuori (48 ore)", "Vacanza (7 giorni)"]

    def __init__(self, coordinator, entry):
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_house_situation"
        self._set_device_info(entry)

    @property
    def current_option(self):
        from .house_climate_plan import situation
        from homeassistant.util import dt as dt_util
        mode = situation({"exception": self.coordinator._house_exception}, dt_util.now())
        return {"normal": self.options[0], "home": self.options[1], "away": self.options[2], "weekend_away": self.options[3], "holiday": self.options[4]}.get(mode, self.options[0])

    async def async_select_option(self, option):
        if option not in self.options:
            raise ValueError("Situazione casa non valida")
        index = self.options.index(option)
        await self.coordinator.async_set_house_exception(["normal", "home", "away", "weekend_away", "holiday"][index], [1, 12, 4, 48, 168][index])
        self.async_write_ha_state()

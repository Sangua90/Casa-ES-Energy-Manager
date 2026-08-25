"""Sensor platform for Casa ES Energy Manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION
from .coordinator import CasaESEnergyCoordinator


@dataclass(frozen=True, kw_only=True)
class CasaESSensorDescription:
    key: str
    name: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None


SENSORS = (
    CasaESSensorDescription(
        key="solar_after_house_w",
        name="FV dopo i carichi casa",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="grid_import_w",
        name="Prelievo rete",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="grid_headroom_w",
        name="Margine rete",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="inverter_headroom_w",
        name="Margine inverter",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="battery_charge_w",
        name="Potenza carica batteria",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="battery_discharge_w",
        name="Potenza scarica batteria",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="battery_soc",
        name="SOC batteria",
        unit=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="phase_l1_headroom_w",
        name="Margine fase L1",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="phase_l2_headroom_w",
        name="Margine fase L2",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(
        key="phase_l3_headroom_w",
        name="Margine fase L3",
        unit=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    CasaESSensorDescription(key="status", name="Stato gestore energia"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Casa ES sensors."""
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CasaESEnergySensor(coordinator, entry, description) for description in SENSORS
    )


class CasaESEnergySensor(CoordinatorEntity[CasaESEnergyCoordinator], SensorEntity):
    """A calculated Casa ES Energy Manager sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CasaESEnergyCoordinator,
        entry: ConfigEntry,
        description: CasaESSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Casa ES",
            model="Energy Manager",
            sw_version=VERSION,
        )

    @property
    def native_value(self) -> Any:
        """Return the calculated value."""
        return self.coordinator.data.get(self.description.key)

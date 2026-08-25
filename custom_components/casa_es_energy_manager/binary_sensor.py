"""Binary sensor platform for Casa ES Energy Manager."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, VERSION
from .coordinator import CasaESEnergyCoordinator


@dataclass(frozen=True, kw_only=True)
class CasaESBinaryDescription:
    key: str
    name: str


BINARY_SENSORS = (
    CasaESBinaryDescription(key="grid_warning", name="Allarme limite rete"),
    CasaESBinaryDescription(key="phase_warning", name="Allarme limite fase"),
    CasaESBinaryDescription(key="inverter_warning", name="Allarme limite inverter"),
    CasaESBinaryDescription(
        key="pv_curtailment_likely",
        name="Limitazione FV probabile",
    ),
    CasaESBinaryDescription(
        key="ai_allow_flexible_loads",
        name="AI consiglia carichi flessibili",
    ),
    CasaESBinaryDescription(
        key="ai_grid_charge_recommended",
        name="AI consiglia ricarica rete",
    ),
    CasaESBinaryDescription(
        key="ai_guardrail_applied",
        name="Correzione guardrail AI applicata",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Casa ES binary sensors."""
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CasaESBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class CasaESBinarySensor(CoordinatorEntity[CasaESEnergyCoordinator], BinarySensorEntity):
    """A Casa ES protection warning or read-only recommendation."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CasaESEnergyCoordinator,
        entry: ConfigEntry,
        description: CasaESBinaryDescription,
    ) -> None:
        super().__init__(coordinator)
        self.description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Casa ES",
            model="Energy Manager",
            sw_version=VERSION,
        )

    @property
    def is_on(self) -> bool:
        """Return whether the state is active."""
        return bool(self.coordinator.data.get(self.description.key, False))

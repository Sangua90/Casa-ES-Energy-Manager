"""Sensor platform for Casa ES Energy Manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
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
    attributes_key: str | None = None


_POWER = {
    "unit": UnitOfPower.WATT,
    "device_class": SensorDeviceClass.POWER,
    "state_class": SensorStateClass.MEASUREMENT,
}
_ENERGY = {
    "unit": UnitOfEnergy.KILO_WATT_HOUR,
    "device_class": SensorDeviceClass.ENERGY,
    "state_class": SensorStateClass.MEASUREMENT,
}

SENSORS = (
    CasaESSensorDescription(key="solar_after_house_w", name="FV misurato dopo i carichi casa", **_POWER),
    CasaESSensorDescription(key="pv_potential_w", name="Potenza FV potenziale stimata", **_POWER),
    CasaESSensorDescription(key="pv_potential_gap_w", name="Potenziale FV non sfruttato stimato", **_POWER),
    CasaESSensorDescription(key="pv_potential_after_house_w", name="FV potenziale dopo i carichi casa", **_POWER),
    CasaESSensorDescription(key="forecast_remaining_kwh", name="Previsione FV residua oggi", **_ENERGY),
    CasaESSensorDescription(key="forecast_current_hour_power_w", name="Potenza FV prevista ora corrente", **_POWER),
    CasaESSensorDescription(key="forecast_current_hour_kwh", name="Energia FV prevista ora corrente", **_ENERGY),
    CasaESSensorDescription(key="forecast_next_hour_power_w", name="Potenza FV prevista prossima ora", **_POWER),
    CasaESSensorDescription(key="forecast_next_hour_kwh", name="Energia FV prevista prossima ora", **_ENERGY),
    CasaESSensorDescription(key="forecast_today_kwh", name="Previsione FV totale oggi", **_ENERGY),
    CasaESSensorDescription(key="forecast_tomorrow_kwh", name="Previsione FV domani", **_ENERGY),
    CasaESSensorDescription(key="grid_import_w", name="Prelievo rete", **_POWER),
    CasaESSensorDescription(key="grid_headroom_w", name="Margine rete", **_POWER),
    CasaESSensorDescription(key="inverter_headroom_w", name="Margine inverter", **_POWER),
    CasaESSensorDescription(key="battery_charge_w", name="Potenza carica batteria", **_POWER),
    CasaESSensorDescription(key="battery_discharge_w", name="Potenza scarica batteria", **_POWER),
    CasaESSensorDescription(key="battery_soc", name="SOC batteria", unit=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    CasaESSensorDescription(key="phase_l1_headroom_w", name="Margine fase L1", **_POWER),
    CasaESSensorDescription(key="phase_l2_headroom_w", name="Margine fase L2", **_POWER),
    CasaESSensorDescription(key="phase_l3_headroom_w", name="Margine fase L3", **_POWER),
    CasaESSensorDescription(key="monitored_load_count", name="Carichi monitorati"),
    CasaESSensorDescription(key="phase_known_load_l1_w", name="Carichi riconosciuti fase L1", **_POWER),
    CasaESSensorDescription(key="phase_known_load_l2_w", name="Carichi riconosciuti fase L2", **_POWER),
    CasaESSensorDescription(key="phase_known_load_l3_w", name="Carichi riconosciuti fase L3", **_POWER),
    CasaESSensorDescription(key="phase_other_load_l1_w", name="Altri carichi fase L1", **_POWER),
    CasaESSensorDescription(key="phase_other_load_l2_w", name="Altri carichi fase L2", **_POWER),
    CasaESSensorDescription(key="phase_other_load_l3_w", name="Altri carichi fase L3", **_POWER),
    CasaESSensorDescription(key="status", name="Stato gestore energia"),
    CasaESSensorDescription(key="ai_status", name="Stato planner AI"),
    CasaESSensorDescription(key="ai_strategy", name="Strategia AI finale"),
    CasaESSensorDescription(key="ai_raw_strategy", name="Strategia AI grezza"),
    CasaESSensorDescription(key="ai_reason", name="Motivazione AI finale"),
    CasaESSensorDescription(key="ai_guardrail_reason", name="Motivo correzione guardrail AI"),
    CasaESSensorDescription(key="ai_last_update", name="Ultimo aggiornamento AI"),
    CasaESSensorDescription(key="ai_battery_reserve_w", name="Riserva batteria consigliata AI", **_POWER),
    CasaESSensorDescription(key="ai_confidence", name="Confidenza AI", unit=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    CasaESSensorDescription(key="battery_energy_needed_kwh", name="Energia netta necessaria batteria al target", **_ENERGY),
    CasaESSensorDescription(key="battery_input_energy_needed_kwh", name="Energia FV richiesta per caricare la batteria", **_ENERGY),
    CasaESSensorDescription(key="base_load_energy_to_target_kwh", name="Consumo base previsto fino al target", **_ENERGY),
    CasaESSensorDescription(key="forecast_energy_to_target_kwh", name="FV previsto fino al target", **_ENERGY),
    CasaESSensorDescription(key="forecast_margin_before_base_load_kwh", name="Margine FV al target prima dei carichi base", **_ENERGY),
    CasaESSensorDescription(key="forecast_margin_after_base_load_kwh", name="Margine FV al target dopo i carichi base", **_ENERGY),
    CasaESSensorDescription(key="flexible_energy_budget_kwh", name="Budget energia carichi flessibili", **_ENERGY),
    CasaESSensorDescription(key="planner_target_reachability", name="Raggiungibilità target batteria"),
    CasaESSensorDescription(key="planner_grid_pressure", name="Pressione elettrica planner"),
    CasaESSensorDescription(key="planner_solar_state", name="Stato FV planner"),
    CasaESSensorDescription(
        key="dry_run_status",
        name="Stato dry-run dispositivi",
        attributes_key="dry_run_decisions",
    ),
    CasaESSensorDescription(key="managed_device_count", name="Dispositivi gestiti dry-run"),
    CasaESSensorDescription(key="managed_devices_running", name="Dispositivi gestiti già attivi"),
    CasaESSensorDescription(key="managed_devices_admissible_now", name="Dispositivi ammessi ora dry-run"),
    CasaESSensorDescription(key="managed_devices_waiting", name="Dispositivi in attesa dry-run"),
    CasaESSensorDescription(key="dry_run_solar_opportunity_w", name="Potenza FV disponibile per nuovi carichi dry-run", **_POWER),
    CasaESSensorDescription(key="dry_run_running_energy_commitment_kwh", name="Impegno energia dispositivi già attivi", **_ENERGY),
    CasaESSensorDescription(key="dry_run_remaining_flexible_budget_kwh", name="Budget flessibile residuo dry-run", **_ENERGY),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: CasaESEnergyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        CasaESEnergySensor(coordinator, entry, description) for description in SENSORS
    )


class CasaESEnergySensor(CoordinatorEntity[CasaESEnergyCoordinator], SensorEntity):
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
        return self.coordinator.data.get(self.description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose detailed per-device dry-run decisions on the summary sensor."""
        if not self.description.attributes_key:
            return None
        value = self.coordinator.data.get(self.description.attributes_key)
        return {"devices": value if isinstance(value, list) else []}

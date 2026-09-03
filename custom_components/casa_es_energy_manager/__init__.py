"""Casa ES Energy Manager integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .ai_planner_v1 import CasaESAIPlanner
from .const import (
    CONF_DEVICE_MIN_OFF_MINUTES,
    CONF_DEVICE_MIN_ON_MINUTES,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_CLIMATE,
    DOMAIN,
    SUBENTRY_TYPE_MANAGED_DEVICE,
)
from .coordinator_v1515 import CasaESEnergyCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SWITCH]
CONF_DEVICE_STOP_PERSISTENCE_MINUTES = "stop_persistence_minutes"


def _persist_climate_anti_cycle_migration(hass: HomeAssistant, entry: ConfigEntry) -> int:
    """Persist the 20/20/20 climate/PDC anti-chatter profile."""
    migrated = 0
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MANAGED_DEVICE:
            continue
        data = dict(subentry.data)
        if str(data.get(CONF_DEVICE_TYPE) or "") != DEVICE_TYPE_CLIMATE:
            continue
        changed = False
        try:
            min_on = float(data.get(CONF_DEVICE_MIN_ON_MINUTES) or 0.0)
            min_off = float(data.get(CONF_DEVICE_MIN_OFF_MINUTES) or 0.0)
        except (TypeError, ValueError):
            min_on = min_off = 0.0
        if min_on <= 0:
            data[CONF_DEVICE_MIN_ON_MINUTES] = 20.0
            min_on = 20.0
            changed = True
        if min_off <= 0 or (abs(min_on - 20.0) < 1e-9 and abs(min_off - 5.0) < 1e-9):
            data[CONF_DEVICE_MIN_OFF_MINUTES] = 20.0
            changed = True
        if data.get(CONF_DEVICE_STOP_PERSISTENCE_MINUTES) in (None, ""):
            data[CONF_DEVICE_STOP_PERSISTENCE_MINUTES] = 20.0
            changed = True
        if changed:
            hass.config_entries.async_update_subentry(entry, subentry, data=data)
            migrated += 1
    return migrated


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _persist_climate_anti_cycle_migration(hass, entry)
    coordinator = CasaESEnergyCoordinator(hass, entry)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()
    planner = CasaESAIPlanner(hass, entry, coordinator)
    coordinator.ai_planner = planner
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if planner.enabled:
        entry.async_on_unload(async_track_time_interval(hass, planner.async_refresh, planner.interval))
        entry.async_on_unload(async_call_later(hass, 5, planner.async_refresh))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        await coordinator.async_prepare_unload()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded

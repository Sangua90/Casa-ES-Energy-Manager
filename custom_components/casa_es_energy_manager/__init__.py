"""Casa ES Energy Manager integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .ai_planner import CasaESAIPlanner
from .const import DOMAIN
from .coordinator import CasaESEnergyCoordinator

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Casa ES Energy Manager from a config entry."""
    coordinator = CasaESEnergyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    planner = CasaESAIPlanner(hass, entry, coordinator)
    coordinator.ai_planner = planner

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if planner.enabled:
        entry.async_on_unload(
            async_track_time_interval(hass, planner.async_refresh, planner.interval)
        )
        entry.async_on_unload(async_call_later(hass, 5, planner.async_refresh))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

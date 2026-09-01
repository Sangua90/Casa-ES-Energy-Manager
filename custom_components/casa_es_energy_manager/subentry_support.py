"""Subentry support for Casa ES Energy Manager."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentryFlow
from homeassistant.core import callback

from .const import SUBENTRY_TYPE_MANAGED_DEVICE, SUBENTRY_TYPE_MONITORED_LOAD
from .managed_device_flow_v1513 import ManagedDeviceSubentryFlow
from .monitored_load_flow import MonitoredLoadSubentryFlow as LegacyMonitoredLoadSubentryFlow
from .monitored_load_flow_v157 import MonitoredLoadSubentryFlow as V157MonitoredLoadSubentryFlow


class MonitoredLoadSubentryFlow(
    V157MonitoredLoadSubentryFlow, LegacyMonitoredLoadSubentryFlow
):
    """v1.5.7 flow while retaining the previous monitored-flow contract."""


class CasaESSubentrySupport:
    """Expose all repeatable Casa ES subentry types."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            SUBENTRY_TYPE_MANAGED_DEVICE: ManagedDeviceSubentryFlow,
            SUBENTRY_TYPE_MONITORED_LOAD: MonitoredLoadSubentryFlow,
        }

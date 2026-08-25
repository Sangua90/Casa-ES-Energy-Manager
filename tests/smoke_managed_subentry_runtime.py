"""Runtime smoke test for the first managed-device config-subentry form.

This intentionally imports the real Home Assistant runtime. It is executed by a
separate GitHub Actions job pinned to the Home Assistant version used by Casa ES.
"""

from __future__ import annotations

import asyncio

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import config_validation as cv
from voluptuous_serialize import convert

from custom_components.casa_es_energy_manager.const import SUBENTRY_TYPE_MANAGED_DEVICE
from custom_components.casa_es_energy_manager.managed_device_flow_v1 import (
    ManagedDeviceSubentryFlow,
)
from custom_components.casa_es_energy_manager.subentry_support import CasaESSubentrySupport


async def _run() -> None:
    supported = CasaESSubentrySupport.async_get_supported_subentry_types(None)
    assert supported[SUBENTRY_TYPE_MANAGED_DEVICE] is ManagedDeviceSubentryFlow

    flow = supported[SUBENTRY_TYPE_MANAGED_DEVICE]()
    flow.init_step = SOURCE_USER

    # This is the exact first step requested by the HA subentry manager for a new
    # subentry. It must exist and produce a serializable form instead of a 400.
    step = getattr(flow, f"async_step_{flow.init_step}")
    result = await step(None)
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == SOURCE_USER
    assert result["data_schema"] is not None

    serialized = convert(
        result["data_schema"], custom_serializer=cv.custom_serializer
    )
    assert serialized
    names = {field["name"] for field in serialized}
    assert {"name", "entity_id", "power_sensor", "nominal_power_w"} <= names


if __name__ == "__main__":
    asyncio.run(_run())

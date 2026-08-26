"""Runtime smoke test for Casa ES managed and monitored subentry forms.

This intentionally imports the real Home Assistant runtime. It is executed by a
separate GitHub Actions job pinned to the Home Assistant version used by Casa ES.
"""

from __future__ import annotations

import asyncio

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import config_validation as cv
from voluptuous_serialize import convert

from custom_components.casa_es_energy_manager.config_flow_v1 import (
    CasaESEnergyManagerConfigFlow,
)
from custom_components.casa_es_energy_manager.const import (
    SUBENTRY_TYPE_MANAGED_DEVICE,
    SUBENTRY_TYPE_MONITORED_LOAD,
)
from custom_components.casa_es_energy_manager.managed_device_flow_v1 import (
    ManagedDeviceSubentryFlow,
)
from custom_components.casa_es_energy_manager.monitored_load_flow import (
    MonitoredLoadSubentryFlow,
)


async def _run() -> None:
    # Exercise the actual ConfigFlow class used by Home Assistant, including its
    # MRO/subentry registration, not only the helper mixin in isolation.
    supported = CasaESEnergyManagerConfigFlow.async_get_supported_subentry_types(None)

    managed_type = supported[SUBENTRY_TYPE_MANAGED_DEVICE]
    assert issubclass(managed_type, ManagedDeviceSubentryFlow)
    flow = managed_type()
    flow.init_step = SOURCE_USER
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
    required = {
        "name",
        "entity_id",
        "power_sensor",
        "nominal_power_w",
        "priority",
        "expected_runtime_minutes",
        "min_on_minutes",
        "min_off_minutes",
    }
    assert required <= names

    removed = {
        "requires_entity",
        "dynamic_current",
        "current_entity",
        "min_current_a",
        "max_current_a",
        "ev_soc_sensor",
        "ev_connected_sensor",
        "ev_target_soc",
    }
    assert removed.isdisjoint(names)

    by_name = {field["name"]: field for field in serialized}
    for name in ("expected_runtime_minutes", "min_on_minutes", "min_off_minutes"):
        assert not by_name[name].get("required", False)

    # v1.4 keeps monitored loads as one category. Emergency control and resume
    # are optional capabilities inside that same form.
    monitored_type = supported[SUBENTRY_TYPE_MONITORED_LOAD]
    assert issubclass(monitored_type, MonitoredLoadSubentryFlow)
    monitored_flow = monitored_type()
    monitored_flow.init_step = SOURCE_USER
    monitored_step = getattr(monitored_flow, f"async_step_{SOURCE_USER}")
    monitored_result = await monitored_step(None)
    assert monitored_result["type"] is data_entry_flow.FlowResultType.FORM
    assert monitored_result["step_id"] == SOURCE_USER

    monitored_serialized = convert(
        monitored_result["data_schema"], custom_serializer=cv.custom_serializer
    )
    monitored_by_name = {
        field["name"]: field for field in monitored_serialized
    }
    assert {
        "name",
        "power_sensor",
        "phase",
        "enabled",
        "emergency_entity",
        "resume_entity",
    } <= set(monitored_by_name)
    assert not monitored_by_name["emergency_entity"].get("required", False)
    assert not monitored_by_name["resume_entity"].get("required", False)


if __name__ == "__main__":
    asyncio.run(_run())

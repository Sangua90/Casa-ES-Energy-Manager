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
    CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
    CONF_MONITORED_LOAD_EMERGENCY_MODE,
    CONF_MONITORED_LOAD_RESUME_ENTITY,
    MONITORED_EMERGENCY_MODE_PAUSE_RESUME,
    MONITORED_EMERGENCY_MODE_STOP_ONLY,
    MONITORED_EMERGENCY_MODE_SWITCH,
    SUBENTRY_TYPE_MANAGED_DEVICE,
    SUBENTRY_TYPE_MONITORED_LOAD,
)
from custom_components.casa_es_energy_manager.managed_device_flow_v1 import (
    ManagedDeviceSubentryFlow,
)
from custom_components.casa_es_energy_manager.monitored_load_flow import (
    MonitoredLoadSubentryFlow,
)


def _serialize(result):
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["data_schema"] is not None
    return convert(result["data_schema"], custom_serializer=cv.custom_serializer)


async def _run() -> None:
    # Exercise the actual ConfigFlow class used by Home Assistant, including its
    # MRO/subentry registration, not only the helper mixin in isolation.
    supported = CasaESEnergyManagerConfigFlow.async_get_supported_subentry_types(None)

    managed_type = supported[SUBENTRY_TYPE_MANAGED_DEVICE]
    assert issubclass(managed_type, ManagedDeviceSubentryFlow)
    flow = managed_type()
    flow.init_step = SOURCE_USER
    result = await getattr(flow, f"async_step_{flow.init_step}")(None)
    assert result["step_id"] == SOURCE_USER
    serialized = _serialize(result)
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

    # v1.4.3 keeps one monitored-load category but exposes a guided emergency
    # control path instead of two ambiguous optional command fields.
    monitored_type = supported[SUBENTRY_TYPE_MONITORED_LOAD]
    assert issubclass(monitored_type, MonitoredLoadSubentryFlow)
    monitored_flow = monitored_type()
    monitored_flow.init_step = SOURCE_USER
    monitored_result = await getattr(monitored_flow, f"async_step_{SOURCE_USER}")(None)
    assert monitored_result["step_id"] == SOURCE_USER
    monitored_serialized = _serialize(monitored_result)
    monitored_names = {field["name"] for field in monitored_serialized}
    assert monitored_names == {
        "name",
        "power_sensor",
        "phase",
        "enabled",
        "emergency_control_enabled",
    }

    # The mode selector must expose the three user-facing control semantics.
    monitored_flow._pending = {}
    mode_result = await monitored_flow.async_step_emergency_type(None)
    assert mode_result["step_id"] == "emergency_type"
    mode_fields = _serialize(mode_result)
    assert {field["name"] for field in mode_fields} == {"emergency_control_mode"}

    # Switch mode: one required switch, no separate resume field.
    monitored_flow._pending = {
        CONF_MONITORED_LOAD_EMERGENCY_MODE: MONITORED_EMERGENCY_MODE_SWITCH,
        CONF_MONITORED_LOAD_EMERGENCY_ENTITY: "switch.stufetta",
    }
    switch_result = await monitored_flow.async_step_emergency_switch(None)
    assert switch_result["step_id"] == "emergency_switch"
    switch_fields = _serialize(switch_result)
    switch_by_name = {field["name"]: field for field in switch_fields}
    assert set(switch_by_name) == {"emergency_entity"}
    assert switch_by_name["emergency_entity"].get("required", False)

    # Pause/resume mode: both commands are required.
    monitored_flow._pending = {
        CONF_MONITORED_LOAD_EMERGENCY_MODE: MONITORED_EMERGENCY_MODE_PAUSE_RESUME,
        CONF_MONITORED_LOAD_EMERGENCY_ENTITY: "button.pause",
        CONF_MONITORED_LOAD_RESUME_ENTITY: "button.resume",
    }
    pause_result = await monitored_flow.async_step_emergency_pause_resume(None)
    assert pause_result["step_id"] == "emergency_pause_resume"
    pause_fields = _serialize(pause_result)
    pause_by_name = {field["name"]: field for field in pause_fields}
    assert set(pause_by_name) == {"emergency_entity", "resume_entity"}
    assert all(field.get("required", False) for field in pause_by_name.values())

    # Stop-only mode: one required stop command and no automatic resume.
    monitored_flow._pending = {
        CONF_MONITORED_LOAD_EMERGENCY_MODE: MONITORED_EMERGENCY_MODE_STOP_ONLY,
        CONF_MONITORED_LOAD_EMERGENCY_ENTITY: "button.stop",
    }
    stop_result = await monitored_flow.async_step_emergency_stop_only(None)
    assert stop_result["step_id"] == "emergency_stop_only"
    stop_fields = _serialize(stop_result)
    assert {field["name"] for field in stop_fields} == {"emergency_entity"}


if __name__ == "__main__":
    asyncio.run(_run())

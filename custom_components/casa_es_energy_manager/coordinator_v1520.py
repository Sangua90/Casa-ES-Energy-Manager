"""DHW recorder planning, early GREEN recovery and PV-only owned Boost."""
from __future__ import annotations

from datetime import timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.components.recorder.history import get_significant_states
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .coordinator_v1519 import CasaESEnergyCoordinator as PreviousCoordinator
from .managed_device_flow_v15 import (
    CONF_THERMAL_BASE_TEMP_C, CONF_THERMAL_NORMAL_MAX_TEMP_C,
    CONF_THERMAL_HARD_MAX_TEMP_C, CONF_THERMAL_BOOST_ENTITY,
    CONF_THERMAL_HEATING_ENTITY, CONF_THERMAL_LEGIONELLA_ENTITY,
    CONF_THERMAL_NOTIFY_SERVICE,
)
from .thermal_history_plan import number, temperature, reconstruct, plan, forecast
from .dhw_recovery_consent import DHWRecoveryConsent

_LOGGER = logging.getLogger(__name__)


class RecorderThermalProfiles:
    """Compatibility view; never blend old synthetic draws with recorder data."""
    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def async_load(self):
        pass

    async def async_save(self):
        pass

    async def async_observe(self, devices):
        pass

    def profile(self, sid):
        model = self.coordinator._dhw_models.get(sid, {})
        return {**model, "heat_pump_c_per_h": model.get("green_c_per_h"),
                "recent_30d_observed_days": model.get("completed_observed_days", 0)}

    def recent_draw_days(self, sid):
        return self.coordinator._dhw_models.get(sid, {}).get("completed_observed_days", 0)

    def expected_draw_c_recent(self, sid, start_hour, end_hour=24):
        hourly, _ = forecast(self.coordinator._dhw_models.get(sid, {}), dt_util.now())
        return sum(hourly[max(start_hour, 0):min(end_hour, 24)])

    expected_draw_c = expected_draw_c_recent

    def export(self):
        return {sid: self.profile(sid) for sid in self.coordinator._dhw_models}


class CasaESEnergyCoordinator(PreviousCoordinator):
    """Keep native PDC on; use full recorder attributes instead of synthetic draws."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._dhw_models: dict[str, dict] = {}
        self._dhw_plans: dict[str, dict] = {}
        self._dhw_history_next = None
        self._dhw_history_error: str | None = None
        self.thermal_learner = RecorderThermalProfiles(self)
        self._dhw_boost_last_stop = {}
        self._dhw_store = Store(hass, 1, f"casa_es_energy_manager.{entry.entry_id}.dhw_recorder_profiles")
        self._dhw_consent_store = Store(hass, 1, f"casa_es_energy_manager.{entry.entry_id}.dhw_recovery_consent")
        self._dhw_consent = DHWRecoveryConsent()
        self._dhw_notify_error = None

    async def async_initialize(self) -> None:
        self._dhw_consent = DHWRecoveryConsent(await self._dhw_consent_store.async_load())
        # Recover ownership even after an expired approval so our Boost can be
        # stopped safely on the next update; do not adopt manual appliance Boost.
        for sid, record in self._dhw_consent.records.items():
            if record.get("owned"):
                self._thermal_boost_owned.add(sid)
                self._thermal_target_c[sid] = record["target_c"]
        saved = await self._dhw_store.async_load()
        if isinstance(saved, dict) and isinstance(saved.get("models"), dict):
            self._dhw_models = saved["models"]
        await super().async_initialize()
        self.entry.async_on_unload(self.hass.bus.async_listen(
            "mobile_app_notification_action", self._async_dhw_notification_action))
        await self._refresh_dhw_history()

    def pending_dhw_recoveries(self):
        now = dt_util.now()
        return [(sid, r) for sid, r in self._dhw_consent.records.items()
                if r.get("status") == "pending" and dt_util.parse_datetime(r["expires"]).timestamp() > now.timestamp()]

    async def async_resend_dhw_recovery(self):
        # Explicit user action is required to reopen a declined/expired episode.
        # Never reset an active recovery or adopt a manually started Boost.
        for sid, record in list(self._dhw_consent.records.items()):
            if record.get("status") not in ("declined", "expired", "notification_failed", "completed") or record.get("owned"):
                continue
            self._dhw_consent.records.pop(sid)
        await self._dhw_consent_store.async_save(self._dhw_consent.records)
        await self.async_request_refresh()

    async def async_answer_dhw_recovery(self, yes):
        pending = self.pending_dhw_recoveries()
        if len(pending) != 1:
            return
        _, record = pending[0]
        sid = self._dhw_consent.answer(f"CASA_ES_DHW_{'YES' if yes else 'NO'}_{record['token']}", dt_util.now())
        if sid:
            await self._dhw_consent_store.async_save(self._dhw_consent.records)
            await self.async_request_refresh()

    async def _async_sync_dhw_notices(self):
        notices = getattr(self, "_dhw_notice_states", {})
        self._dhw_notice_states = notices
        for sid, record in self._dhw_consent.records.items():
            status = record.get("status")
            if status == "pending" and dt_util.parse_datetime(record["expires"]).timestamp() <= dt_util.now().timestamp():
                record["status"] = status = "expired"
                await self._dhw_consent_store.async_save(self._dhw_consent.records)
            stamp = (record.get("episode"), status)
            if notices.get(sid) == stamp:
                continue
            if status in ("completed", "declined", "expired"):
                # A restart must not resurrect a finished request. Keep its
                # history in the dashboard, remove only the obsolete notice.
                if self.hass.services.has_service("persistent_notification", "dismiss"):
                    await self.hass.services.async_call("persistent_notification", "dismiss", {
                        "notification_id": f"casa_es_dhw_{sid}"}, blocking=True)
                    notices[sid] = stamp
                continue
            if self.hass.services.has_service("persistent_notification", "create"):
                action = ("[Apri Energy Manager per rispondere Sì o No](/energy-manager/acqua-calda). "
                          if status == "pending" else "[Apri Energy Manager per vedere lo stato](/energy-manager/acqua-calda). ")
                labels = {"pending": "In attesa della tua risposta", "approved": "Resistenza autorizzata", "declined": "Resistenza non autorizzata", "expired": "Richiesta scaduta: nessuna autorizzazione", "completed": "Recupero completato", "notification_failed": "Avviso telefono non consegnato"}
                await self.hass.services.async_call("persistent_notification", "create", {
                    "notification_id": f"casa_es_dhw_{sid}", "title": "Energy Manager · acqua calda",
                    "message": f"{labels.get(status, status)}. Obiettivo: {record.get('target_c')} °C. "
                               + action +
                               "Il consenso è valido solo per questa richiesta e non si rinnova automaticamente."}, blocking=True)
                notices[sid] = stamp

    async def _async_dhw_notification_action(self, event) -> None:
        sid = self._dhw_consent.answer(event.data.get("action"), dt_util.now())
        if sid is None:
            return
        await self._dhw_consent_store.async_save(self._dhw_consent.records)
        await self.async_request_refresh()

    async def _async_request_dhw_recovery(self, item, result, now) -> None:
        service = str(item.get(CONF_THERMAL_NOTIFY_SERVICE, ""))
        if not service.startswith("notify.mobile_app_"):
            return
        service = service.split(".", 1)[1]
        if not self.hass.services.has_service("notify", service):
            self._dhw_notify_error = "Servizio notifica telefono non disponibile"
            return
        deadline = dt_util.parse_datetime(result["deadline"])
        hours = (deadline.timestamp() - now.timestamp()) / 3600
        if not result["recovery_request_due"] or hours < -1:
            return
        if result["green_shortfall_c"] <= 1 or result["target_c"] <= item["thermal_current_temperature_c"] + 1:
            return
        sid = str(item["subentry_id"])
        record = self._dhw_consent.request(sid, result, now)
        if record is None:
            return
        await self._dhw_consent_store.async_save(self._dhw_consent.records)
        token = record["token"]
        try:
            await self.hass.services.async_call("notify", service, {
                "title": "Energy Meter · acqua calda",
                "message": (f"Per gli usi previsti alle {deadline.strftime('%H:%M')} e la riserva "
                            f"del {deadline.strftime('%d/%m')} servono circa {result['target_c']:.0f} °C. Il surplus FV ora non basta. "
                            "Vuoi autorizzare la resistenza, anche usando elettricità dalla rete? "
                            f"Tempo prudente di riscaldamento: {result['boost_heating_hours']:.1f} ore. "
                            "Il consenso vale per un solo recupero e scade al termine del tempo previsto. "
                            "Rispondi entro 45 minuti; senza risposta resta GREEN."),
                "data": {"tag": f"casa_es_dhw_{sid}", "actions": [
                    {"action": f"CASA_ES_DHW_YES_{token}", "title": "Sì, usa la resistenza", "authenticationRequired": True},
                    {"action": f"CASA_ES_DHW_NO_{token}", "title": "No"}]}}, blocking=True)
            self._dhw_notify_error = None
        except Exception as err:
            record["status"] = "notification_failed"
            record["retry_at"] = (now + timedelta(minutes=30)).isoformat()
            self._dhw_notify_error = type(err).__name__
            await self._dhw_consent_store.async_save(self._dhw_consent.records)
            _LOGGER.warning("DHW notification unavailable: %s", type(err).__name__)

    async def _stop_owned_thermal_boost(self, item, reason, now) -> None:
        await super()._stop_owned_thermal_boost(item, reason, now)
        self._dhw_consent.finish(str(item.get("subentry_id", "")))
        await self._dhw_consent_store.async_save(self._dhw_consent.records)

    async def _refresh_dhw_history(self) -> None:
        now = dt_util.now()
        if self._dhw_history_next is not None and now < self._dhw_history_next:
            return
        self._dhw_history_next = now + timedelta(hours=1)
        configs = [s for s in self.entry.subentries.values()
                   if s.data.get("device_type") == "thermal_storage"]
        if not configs:
            return
        entity_ids = set()
        mappings = {}
        for subentry in configs:
            config = subentry.data
            mapping = {"water": config.get("entity_id"),
                       "boost": config.get(CONF_THERMAL_BOOST_ENTITY),
                       "heating": config.get(CONF_THERMAL_HEATING_ENTITY),
                       "legionella": config.get(CONF_THERMAL_LEGIONELLA_ENTITY)}
            if all(mapping.values()):
                mappings[subentry.subentry_id] = mapping
                entity_ids.update(mapping.values())
        if not entity_ids:
            self._dhw_history_error = "Entità termiche mancanti: configurare Boost, riscaldamento e legionella"
            return
        try:
            states = await get_instance(self.hass).async_add_executor_job(partial(
                get_significant_states, self.hass,
                dt_util.as_utc(now - timedelta(days=30)), dt_util.as_utc(now),
                list(entity_ids), include_start_time_state=True,
                significant_changes_only=False, minimal_response=False, no_attributes=False))
            rows = {entity: [{"last_updated": s.last_updated,
                             "state": s.state, "attributes": dict(s.attributes)}
                            for s in values] for entity, values in states.items()}
            unit = self.hass.config.units.temperature_unit
            for subentry_id, mapping in mappings.items():
                model = reconstruct(rows, mapping, now, unit)
                model["entity_ids"] = mapping
                model["timezone"] = str(now.tzinfo)
                if not rows.get(mapping["water"]):
                    model["history_unavailable"] = True
                previous_model = self._dhw_models.get(subentry_id, {})
                previous_days = (previous_model.get("draw_by_day", {})
                                 if previous_model.get("entity_ids") == mapping and previous_model.get("timezone") == str(now.tzinfo)
                                 else {})
                # Retain completed recorder-derived aggregates when recorder has
                # shorter raw retention; replace overlapping dates, never add.
                cutoff = (now - timedelta(days=30)).date().isoformat()
                merged = {d: v for d, v in previous_days.items()
                          if cutoff <= d < now.date().isoformat() and d not in model["invalid_days"]}
                merged.update(model["draw_by_day"])
                model["draw_by_day"] = merged
                model["completed_observed_days"] = len(merged)
                self._dhw_models[subentry_id] = model
            await self._dhw_store.async_save({"models": self._dhw_models})
            self._dhw_history_error = None
        except Exception as err:
            # Keep the previous model, retry soon, and expose failure explicitly.
            self._dhw_history_error = type(err).__name__
            self._dhw_history_next = now + timedelta(minutes=15)
            _LOGGER.warning("DHW recorder history unavailable: %s", type(err).__name__)

    def _thermal_context(self, item: dict[str, Any]) -> dict[str, Any]:
        item = super()._thermal_context(item)
        if item.get("device_type") != "thermal_storage":
            return item
        water = self.hass.states.get(item.get("entity_id", ""))
        if water is None or water.state in ("unknown", "unavailable", "off"):
            item["thermal_current_temperature_c"] = None
        else:
            unit = self.hass.config.units.temperature_unit
            item["thermal_current_temperature_c"] = temperature(water.attributes.get("current_temperature"), unit)
            item["thermal_target_temperature_c"] = temperature(water.attributes.get("temperature"), unit)
        return item

    def _thermal_target(self, item: dict, data: dict, now: Any) -> tuple[float, str]:
        entity = self.hass.states.get(item.get("entity_id", ""))
        attrs = entity.attributes if entity else {}
        unit = self.hass.config.units.temperature_unit
        physical_max = temperature(attrs.get("max_temp"), unit) or 65
        maximum = min(number(item.get(CONF_THERMAL_NORMAL_MAX_TEMP_C), 65),
                      number(item.get(CONF_THERMAL_HARD_MAX_TEMP_C), 70), physical_max)
        base = min(number(item.get(CONF_THERMAL_BASE_TEMP_C), 53), 53, maximum)
        current = number(item.get("thermal_current_temperature_c"), base)
        subentry_id = str(item.get("subentry_id", ""))
        result = plan(self._dhw_models.get(subentry_id, {}), now, current, base, maximum, reserve_in_base=True)
        tomorrow_fv = number(data.get("forecast_tomorrow_kwh"))
        today_fv = number(data.get("forecast_today_kwh"))
        poor_tomorrow = tomorrow_fv is not None and (tomorrow_fv < 6 or (today_fv and tomorrow_fv < today_fv * 0.35))
        result["tomorrow_fv_kwh"] = tomorrow_fv
        result["precharge_for_poor_tomorrow"] = bool(poor_tomorrow)
        if poor_tomorrow and now.hour >= 10:
            morning = sum(result["tomorrow_hourly_draw_c"][:12])
            loss = number(self._dhw_models.get(subentry_id, {}).get("standby_loss_c_per_h"), 0.5)
            required = max(result["required_uncapped_c"], result["minimum_after_use_c"] + result["expected_remaining_draw_c"] + morning + result["reserve_c"] + min(loss * (32 - now.hour), 5))
            result["required_uncapped_c"] = round(required, 1)
            result["target_c"] = round(min(required, maximum), 1)
            result["capacity_shortfall_c"] = round(max(required - maximum, 0), 1)
            result["green_shortfall_c"] = round(max(required - 53, 0), 1)
            deadline = dt_util.parse_datetime(result["deadline"])
            heating_hours = max((result["target_c"] - current) / result["boost_c_per_h"], 0) * 1.25 + 0.5
            lead_hours = heating_hours + 0.75
            result.update(boost_heating_hours=round(heating_hours, 2),
                          recovery_request_lead_hours=round(lead_hours, 2),
                          recovery_request_at=(deadline - timedelta(hours=lead_hours)).isoformat(),
                          recovery_request_due=deadline.timestamp() - now.timestamp() <= lead_hours * 3600)
        self._dhw_plans[subentry_id] = result
        return result["target_c"], (
            f"storico HA: prelievo residuo {result['expected_remaining_draw_c']:.1f}°C; "
            f"riserva fissa {result['reserve_c']:.1f}°C; "
            f"uso previsto {result['deadline']}; anticipo GREEN {result['green_lead_hours']:.1f} h")

    async def _set_water_temperature(self, entity_id: str, value: float) -> None:
        unit = self.hass.config.units.temperature_unit
        service_value = value * 9 / 5 + 32 if unit == "°F" else value
        await super()._set_water_temperature(entity_id, service_value)

    async def _async_call_entity_control(self, entity_id: str, turn_on: bool) -> None:
        # Legacy/misclassified water heaters are protected too.
        if entity_id.startswith("water_heater.") or entity_id == "switch.ariston_power":
            self._thermal_main_entity_commands_blocked += 1
            return
        await super()._async_call_entity_control(entity_id, turn_on)

    async def _async_apply_thermal_control(self, data: dict, now: Any) -> bool:
        """Own this path: legacy below-base logic must not override GREEN planning."""
        if not self.real_control_enabled:
            return False
        for raw in sorted(data.get("managed_device_configs") or [], key=lambda c: int(c.get("priority", 50))):
            if raw.get("device_type") != "thermal_storage" or not raw.get("enabled", True):
                continue
            item = self._thermal_context(dict(raw))
            sid = str(item.get("subentry_id", ""))
            owned = sid in self._thermal_boost_owned
            if item.get("management_mode", "auto") != "auto" or item.get("thermal_legionella_active"):
                continue
            if item.get("thermal_boost_active") and not owned:
                continue
            if owned and not item.get("thermal_boost_active"):
                self._thermal_boost_owned.discard(sid)
                self._thermal_target_c.pop(sid, None)
                self._dhw_boost_last_stop[sid] = now
                owned = False
                if self._dhw_consent.records.get(sid, {}).get("owned"):
                    self._dhw_consent.finish(sid)
                    await self._dhw_consent_store.async_save(self._dhw_consent.records)
            current = item.get("thermal_current_temperature_c")
            if current is None:
                continue
            # Missing safety/status entities are not interpreted as OFF.
            status_ids = [item.get(k) for k in (CONF_THERMAL_BOOST_ENTITY, CONF_THERMAL_HEATING_ENTITY, CONF_THERMAL_LEGIONELLA_ENTITY)]
            if any(not e or self.hass.states.get(e) is None or self.hass.states.get(e).state in ("unknown", "unavailable") for e in status_ids):
                continue
            target, reason = self._thermal_target(item, data, now)
            result = self._dhw_plans[sid]
            approved_target = self._dhw_consent.approved_target(sid, now)
            grid_cycle = self._dhw_consent.records.get(sid, {}).get("owned", False)
            if approved_target is not None:
                # A reply authorizes only the temperature shown in that request.
                target = min(target, approved_target)
                if owned and not grid_cycle:
                    self._dhw_consent.records[sid]["owned"] = True
                    grid_cycle = True
                    await self._dhw_consent_store.async_save(self._dhw_consent.records)
            nominal = max(number(item.get("nominal_power_w"), 1200), 1)
            allocation = data.get("v156_battery_allocation") or self._battery_allocation(data)
            # Export is measured surplus remaining after house AND battery.
            # Potential FV alone is allowed only under the existing clipping guard.
            measured = max(number(data.get("grid_export_w"), 0), 0)
            potential = max(number(data.get("pv_potential_after_house_w"), 0), 0)
            existing_power = max(number(item.get("current_power_w"), 0), 0) if owned else 0
            battery_draw = max(number(data.get("battery_discharge_w"), 0), 0)
            grid_draw = max(number(data.get("grid_import_w"), 0), 0)
            covered_power = max(existing_power - battery_draw - grid_draw, 0)
            available = max(measured + covered_power,
                            potential if battery_draw <= 100 and self._virtual_surplus_opportunity(data) else 0)
            if allocation.get("below_target"):
                available = min(available, max(number(allocation.get("overflow_w"), 0), 0))
            soc_ok = number(data.get("battery_soc"), 0) >= number(item.get("min_battery_soc"), 0)
            phase = str(item.get("phase", "l1"))
            phase_margin = number(data.get(f"phase_{phase}_headroom_w"), None)
            if phase_margin is None:
                phase_margin = number(data.get(f"phase_{phase}_margin_w"), 0)
            electrical_ok = (not data.get("grid_warning") and not data.get("inverter_warning")
                             and phase_margin >= (0 if owned else nominal)
                             and number(data.get("inverter_headroom_w"), 0) >= (0 if owned else nominal))
            pv_ok = available >= nominal * (0.8 if owned else 0.95) and soc_ok and electrical_ok
            approved_ok = approved_target is not None and electrical_ok
            entity_id = str(item.get("entity_id", ""))
            boost_id = str(item.get(CONF_THERMAL_BOOST_ENTITY, ""))
            if owned:
                if current >= target - 0.3 or not electrical_ok or (grid_cycle and approved_target is None) or not (pv_ok or approved_ok):
                    await self._stop_owned_thermal_boost(item, "Target raggiunto o FV misurato insufficiente; ritorno GREEN", now)
                    self._dhw_boost_last_stop[sid] = now
                    return True
                # Raise an in-flight target when tomorrow's demand increases.
                if target > self._thermal_target_c.get(sid, 0) + 0.5:
                    await self._set_water_temperature(entity_id, target)
                    self._thermal_target_c[sid] = target
                    return True
                continue
            last_stop = self._dhw_boost_last_stop.get(sid)
            min_off = max(number(item.get("min_off_minutes"), 5), 5)
            restart_ok = last_stop is None or (now - last_stop).total_seconds() >= min_off * 60
            if (pv_ok or approved_ok) and restart_ok and target > current + 0.5:
                if approved_ok:
                    record = self._dhw_consent.records[sid]
                    record["owned"] = True
                    await self._dhw_consent_store.async_save(self._dhw_consent.records)
                await self._set_water_temperature(entity_id, target)
                await self._set_boost(boost_id, True)
                self._thermal_boost_owned.add(sid)
                self._thermal_target_c[sid] = target
                self._last_thermal_action = "boost_on"
                self._last_thermal_reason = reason
                self._last_thermal_at = now.isoformat()
                return True
            if approved_target is not None and current >= target - 0.3:
                self._dhw_consent.finish(sid)
                await self._dhw_consent_store.async_save(self._dhw_consent.records)
            if not pv_ok and approved_target is None:
                await self._async_request_dhw_recovery(item, result, now)
            # GREEN is capped at the manufacturer's heat-pump limit. It never
            # promises >53 C or quietly substitutes grid-powered resistance.
            water = self.hass.states.get(entity_id)
            green_target = result["green_target_c"]
            if result["green_due"] and water and water.state.upper() != "GREEN" and "GREEN" in water.attributes.get("operation_list", []):
                await self.hass.services.async_call("water_heater", "set_operation_mode", {"entity_id": entity_id, "operation_mode": "GREEN"}, blocking=True)
                return True
            if result["green_due"] and water and water.state.upper() == "GREEN" and abs(number(item.get("thermal_target_temperature_c"), 0) - green_target) >= 0.5:
                await self._set_water_temperature(entity_id, green_target)
                self._last_thermal_action = "green_early_setpoint"
                self._last_thermal_reason = reason
                self._last_thermal_at = now.isoformat()
                return True
        return False

    async def _async_update_data(self) -> dict:
        await self._refresh_dhw_history()
        data = await super()._async_update_data()
        data["dhw_history_error"] = self._dhw_history_error
        data["dhw_notification_error"] = self._dhw_notify_error
        await self._async_sync_dhw_notices()
        data["dhw_recovery_decisions"] = {sid: {k: v for k, v in record.items() if k != "token"}
                                          for sid, record in self._dhw_consent.records.items()}
        data["dhw_plans"] = self._dhw_plans
        data["dhw_history_models"] = self._dhw_models
        diag = data.get("v1511_thermal_adaptive_target")
        if isinstance(diag, dict):
            diag.update(model="recorder_completed_demand_days", margin_c=4.0,
                        adaptive_storage_buffer_c=0.0, adaptive_margin_cap_c=4.0,
                        history_source="home_assistant_recorder",
                        heat_pump_max_c=53.0, current_day_in_training=False)
        data["dhw_plan_devices"] = [{"subentry_id": sid, **value} for sid, value in self._dhw_plans.items()]
        data["dhw_plan_status"] = ("history_unavailable" if self._dhw_history_error else
                                   "capacity_shortfall" if any(p.get("capacity_shortfall_c", 0) > 0 for p in self._dhw_plans.values()) else
                                   "planned" if self._dhw_plans else "unconfigured")
        return data

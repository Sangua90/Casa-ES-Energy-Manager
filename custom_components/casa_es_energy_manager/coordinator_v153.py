"""Casa ES Energy Manager v1.5.3 grid-aware protection and dynamic solar target.

v1.5.3 keeps per-phase power as a balancing/admission signal, but no longer
uses a phase-only overload to shed appliances. Hard emergency shedding remains
reserved for real total-grid or inverter risk. It also records a detailed
snapshot for every electrical intervention and derives the battery target
horizon from the end of useful same-day PV production when a forecast curve is
available.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .coordinator_v152 import CasaESEnergyCoordinator as V152Coordinator
from .daily_target import (
    TARGET_MODE_DAY_COMPLETE,
    TARGET_MODE_DEADLINE,
    TARGET_MODE_RECOVERY,
    daily_battery_target_window,
    solar_recovery_available,
)

DYNAMIC_SOLAR_USEFUL_MIN_W = 500.0
DYNAMIC_SOLAR_TARGET_BUFFER_MINUTES = 30


class CasaESEnergyCoordinator(V152Coordinator):
    """v1.5.3 controller with grid-only emergency shedding and solar deadline."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._last_electrical_intervention_snapshot: dict[str, Any] | None = None

    @staticmethod
    def _number(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _dynamic_solar_window(
        self,
        now: Any,
        source: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build a target window from the end of useful forecast PV production."""
        curve = source.get("forecast_curve") or source.get("forecast_curve_today") or []
        useful: list[Any] = []
        local_day = dt_util.as_local(now).date()

        for point in curve:
            if not isinstance(point, dict):
                continue
            power_w = self._number(point.get("power_w"), -1.0)
            if power_w < DYNAMIC_SOLAR_USEFUL_MIN_W:
                continue
            parsed = dt_util.parse_datetime(str(point.get("time") or ""))
            if parsed is None:
                continue
            parsed = dt_util.as_utc(parsed)
            if dt_util.as_local(parsed).date() != local_day:
                continue
            useful.append(parsed)

        if not useful:
            return None

        solar_end = max(useful)
        deadline = solar_end - timedelta(minutes=DYNAMIC_SOLAR_TARGET_BUFFER_MINUTES)

        if now < deadline:
            return {
                "deadline": deadline,
                "planning_target": deadline,
                "mode": TARGET_MODE_DEADLINE,
                "target_active": True,
                "dynamic": True,
                "solar_useful_end": solar_end,
            }

        if now < solar_end or solar_recovery_available(source):
            planning_target = solar_end if solar_end > now else now + timedelta(minutes=5)
            return {
                "deadline": deadline,
                "planning_target": planning_target,
                "mode": TARGET_MODE_RECOVERY,
                "target_active": True,
                "dynamic": True,
                "solar_useful_end": solar_end,
            }

        return {
            "deadline": deadline,
            "planning_target": now,
            "mode": TARGET_MODE_DAY_COMPLETE,
            "target_active": False,
            "dynamic": True,
            "solar_useful_end": solar_end,
        }

    def _target_window(
        self,
        now: Any | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = now or dt_util.now()
        source = data if data is not None else (self.data or {})
        dynamic = self._dynamic_solar_window(current, source)
        if dynamic is not None:
            return dynamic

        # Forecast curve unavailable: preserve the configured hour as a safe
        # backwards-compatible fallback instead of guessing a solar deadline.
        target_hour = int(self._config("battery_target_hour", 15))
        fallback = daily_battery_target_window(
            current,
            target_hour,
            recovery_solar_available=solar_recovery_available(source),
        )
        fallback["dynamic"] = False
        fallback["solar_useful_end"] = None
        return fallback

    def _electrical_warning_active(self, data: dict[str, Any]) -> bool:
        """Phase-only overload is advisory; grid/inverter remain hard safety."""
        return bool(data.get("grid_warning") or data.get("inverter_warning"))

    def _electrical_snapshot(
        self,
        data: dict[str, Any],
        *,
        reason: str,
        entity_id: str,
        action: str,
        now: Any,
    ) -> dict[str, Any]:
        active_loads = []
        for item in data.get("phase_load_breakdown") or []:
            if not isinstance(item, dict):
                continue
            power_w = self._number(item.get("power_w"), 0.0)
            if power_w <= 20.0:
                continue
            active_loads.append(
                {
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "phase": item.get("phase"),
                    "power_w": round(power_w, 1),
                }
            )

        return {
            "captured_at": now.isoformat(),
            "action": action,
            "entity_id": entity_id,
            "reason": reason,
            "grid_import_w": round(self._number(data.get("grid_import_w")), 1),
            "grid_power_w": round(self._number(data.get("grid_power_w")), 1),
            "load_power_w": round(self._number(data.get("load_power_w")), 1),
            "pv_power_w": round(self._number(data.get("pv_power_w")), 1),
            "pv_potential_w": round(self._number(data.get("pv_potential_w")), 1),
            "battery_soc": round(self._number(data.get("battery_soc")), 1),
            "battery_charge_w": round(self._number(data.get("battery_charge_w")), 1),
            "battery_discharge_w": round(self._number(data.get("battery_discharge_w")), 1),
            "phase_l1_power_w": round(self._number(data.get("phase_l1_power_w")), 1),
            "phase_l2_power_w": round(self._number(data.get("phase_l2_power_w")), 1),
            "phase_l3_power_w": round(self._number(data.get("phase_l3_power_w")), 1),
            "phase_l1_headroom_w": round(self._number(data.get("phase_l1_headroom_w")), 1),
            "phase_l2_headroom_w": round(self._number(data.get("phase_l2_headroom_w")), 1),
            "phase_l3_headroom_w": round(self._number(data.get("phase_l3_headroom_w")), 1),
            "grid_headroom_w": round(self._number(data.get("grid_headroom_w")), 1),
            "inverter_headroom_w": round(self._number(data.get("inverter_headroom_w")), 1),
            "grid_warning": bool(data.get("grid_warning")),
            "phase_warning": bool(data.get("phase_warning")),
            "inverter_warning": bool(data.get("inverter_warning")),
            "hottest_phase": data.get("hottest_phase"),
            "grid_safety_margin_w": round(self._number(data.get("grid_safety_margin_w"), 300.0), 1),
            "phase_safety_margin_w": round(self._number(data.get("phase_safety_margin_w"), 150.0), 1),
            "inverter_safety_margin_w": round(self._number(data.get("inverter_safety_margin_w"), 250.0), 1),
            "active_loads_over_20w": active_loads,
        }

    def _write_emergency_diagnostics(self, data: dict[str, Any], now: Any) -> None:
        super()._write_emergency_diagnostics(data, now)
        diag = data.get("monitored_emergency_control")
        if not isinstance(diag, dict):
            return
        policy = diag.get("policy")
        if isinstance(policy, dict):
            policy["phase_or_inverter"] = "phase_advisory_inverter_hard"
            policy["phase_only_shed"] = "disabled"
        diag["phase_warning_advisory_only"] = bool(data.get("phase_warning"))
        diag["last_intervention_snapshot"] = self._last_electrical_intervention_snapshot
        for item in diag.get("shed_loads") or []:
            if not isinstance(item, dict):
                continue
            record = self._shed_monitored_loads.get(str(item.get("subentry_id") or ""))
            if record and record.get("electrical_snapshot"):
                item["electrical_snapshot"] = record["electrical_snapshot"]

    async def _async_execute_monitored_shed(
        self,
        load: dict[str, Any],
        *,
        reason: str,
        now: Any,
        data: dict[str, Any],
    ) -> bool:
        subentry_id = str(load.get("subentry_id") or "")
        entity_id = str(load.get("emergency_entity") or "")
        snapshot = self._electrical_snapshot(
            data,
            reason=reason,
            entity_id=entity_id,
            action="monitored_shed",
            now=now,
        )
        handled = await super()._async_execute_monitored_shed(
            load, reason=reason, now=now, data=data
        )
        if handled:
            self._last_electrical_intervention_snapshot = snapshot
            record = self._shed_monitored_loads.get(subentry_id)
            if record is not None:
                record["electrical_snapshot"] = snapshot
            self._write_emergency_diagnostics(data, now)
        return handled

    async def _async_execute_managed_stop(
        self,
        decision: dict[str, Any],
        *,
        reason: str | None,
        now: Any,
        data: dict[str, Any],
    ) -> bool:
        final_reason = str(reason or decision.get("reason") or "Protezione Casa ES")
        entity_id = str(decision.get("entity_id") or "")
        snapshot = self._electrical_snapshot(
            data,
            reason=final_reason,
            entity_id=entity_id,
            action="managed_stop",
            now=now,
        )
        handled = await super()._async_execute_managed_stop(
            decision, reason=reason, now=now, data=data
        )
        if handled:
            self._last_electrical_intervention_snapshot = snapshot
            self._write_emergency_diagnostics(data, now)
        return handled

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        """Mask phase-only hard safety while preserving phase-aware admission."""
        original_phase_warning = bool(data.get("phase_warning"))
        altered: list[tuple[dict[str, Any], bool, bool, Any, Any]] = []

        # device_dry_run_v1 historically converted phase_warning into an immediate
        # hard stop. If there is no inverter warning, neutralize only that hard
        # stop for real control. Start admission still uses the original phase
        # headrooms computed earlier, so balancing behavior is preserved.
        if original_phase_warning and not data.get("inverter_warning"):
            for decision in data.get("dry_run_decisions") or []:
                if not isinstance(decision, dict) or not decision.get("stop_is_hard_safety"):
                    continue
                altered.append(
                    (
                        decision,
                        bool(decision.get("would_stop")),
                        bool(decision.get("stop_is_hard_safety")),
                        decision.get("decision"),
                        decision.get("reason"),
                    )
                )
                decision["would_stop"] = False
                decision["stop_is_hard_safety"] = False
                decision["decision"] = "phase_advisory"
                decision["reason"] = (
                    "Fase oltre il margine inverter: segnale usato per bilanciamento, "
                    "nessuno sgancio immediato senza rischio rete totale o inverter."
                )

        data["phase_warning"] = False
        data["phase_warning_raw"] = original_phase_warning
        data["phase_warning_control_mode"] = "balancing_advisory_only"
        try:
            await super()._async_apply_real_control(data, now)
        finally:
            data["phase_warning"] = original_phase_warning
            for decision, would_stop, hard, old_decision, old_reason in altered:
                decision["would_stop"] = would_stop
                decision["stop_is_hard_safety"] = hard
                decision["decision"] = old_decision
                decision["reason"] = old_reason

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        now = dt_util.now()
        window = self._target_window(now, data)
        data["v153_phase_protection_mode"] = "balancing_advisory_only"
        data["v153_hard_safety_sources"] = ["grid_total", "inverter_total"]
        data["v153_last_electrical_intervention_snapshot"] = self._last_electrical_intervention_snapshot
        data["battery_target_dynamic_from_solar_end"] = bool(window.get("dynamic"))
        data["battery_target_solar_useful_min_w"] = DYNAMIC_SOLAR_USEFUL_MIN_W
        data["battery_target_safety_buffer_minutes"] = DYNAMIC_SOLAR_TARGET_BUFFER_MINUTES
        data["battery_target_solar_useful_end"] = (
            window["solar_useful_end"].isoformat()
            if window.get("solar_useful_end") is not None
            else None
        )
        return data

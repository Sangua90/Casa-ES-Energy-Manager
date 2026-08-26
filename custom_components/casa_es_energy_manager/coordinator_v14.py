"""v1.4 coordinator: monitored loads may shed only for electrical protection."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_ON_ONLY,
    CONF_GRID_POWER_LIMIT,
    CONF_INVERTER_POWER_LIMIT,
    CONF_MONITORED_LOAD_EMERGENCY_ENTITY,
    CONF_MONITORED_LOAD_NAME,
    CONF_MONITORED_LOAD_PHASE,
    CONF_MONITORED_LOAD_RESUME_ENTITY,
    CONF_PHASE_POWER_LIMIT,
    CONF_SAFETY_MARGIN,
    DEFAULT_GRID_POWER_LIMIT,
    DEFAULT_INVERTER_POWER_LIMIT,
    DEFAULT_PHASE_POWER_LIMIT,
    DEFAULT_SAFETY_MARGIN,
    DEVICE_MODE_AUTO,
    DEVICE_MODE_OFF,
    DEVICE_MODE_OVERRIDE,
    MONITORED_EMERGENCY_ACTIVE_POWER_THRESHOLD_W,
    MONITORED_EMERGENCY_RECOVERY_STABLE_SECONDS,
)
from .coordinator_v1 import CasaESEnergyCoordinator as V12Coordinator
from .monitored_emergency import (
    choose_relief_candidate,
    eligible_emergency_loads,
    grid_relief_w,
    inverter_relief_w,
    most_overloaded_phase,
    warning_phases,
)


class CasaESEnergyCoordinator(V12Coordinator):
    """v1.4 controller with optional emergency-only monitored-load commands."""

    def __init__(self, hass: Any, entry: Any) -> None:
        super().__init__(hass, entry)
        self._shed_monitored_loads: dict[str, dict[str, Any]] = {}
        self._electrical_safe_since = None
        self._manual_restore_notified: set[str] = set()

    def _safe_limits(self) -> tuple[float, float, float]:
        margin = float(self._config(CONF_SAFETY_MARGIN, DEFAULT_SAFETY_MARGIN))
        return (
            max(
                float(self._config(CONF_GRID_POWER_LIMIT, DEFAULT_GRID_POWER_LIMIT))
                - margin,
                0.0,
            ),
            max(
                float(
                    self._config(
                        CONF_INVERTER_POWER_LIMIT, DEFAULT_INVERTER_POWER_LIMIT
                    )
                )
                - margin,
                0.0,
            ),
            max(
                float(self._config(CONF_PHASE_POWER_LIMIT, DEFAULT_PHASE_POWER_LIMIT))
                - margin,
                0.0,
            ),
        )

    async def _async_call_monitored_control(
        self, entity_id: str, *, resume: bool
    ) -> str:
        """Call an emergency control entity using safe domain semantics."""
        domain = entity_id.split(".", 1)[0]
        if domain == "button":
            service_domain = "button"
            service = "press"
        elif domain == "script":
            service_domain = "script"
            service = "turn_on"
        else:
            service = "turn_on" if resume else "turn_off"
            if self.hass.services.has_service(domain, service):
                service_domain = domain
            elif self.hass.services.has_service("homeassistant", service):
                service_domain = "homeassistant"
            else:
                raise HomeAssistantError(
                    f"Nessun servizio {service} disponibile per {entity_id}."
                )

        if not self.hass.services.has_service(service_domain, service):
            raise HomeAssistantError(
                f"Servizio {service_domain}.{service} non disponibile per {entity_id}."
            )
        await self.hass.services.async_call(
            service_domain,
            service,
            {"entity_id": entity_id},
            blocking=True,
        )
        return f"{service_domain}.{service}"

    def _record_real_command(
        self,
        *,
        entity_id: str,
        action: str,
        reason: str,
        now: Any,
        error: str | None = None,
    ) -> None:
        self._last_command_at[entity_id] = now
        self._last_real_control_action = action
        self._last_real_control_entity = entity_id
        self._last_real_control_reason = reason
        self._last_real_control_at = now.isoformat()
        self._last_real_control_error = error

    def _electrical_warning_active(self, data: dict[str, Any]) -> bool:
        return bool(
            data.get("grid_warning")
            or data.get("phase_warning")
            or data.get("inverter_warning")
        )

    def _refresh_shed_records(self, data: dict[str, Any]) -> None:
        """Drop stale records and notice manual restoration of stateful entities."""
        loads = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("monitored_loads") or [])
            if item.get("subentry_id")
        }
        for subentry_id, record in list(self._shed_monitored_loads.items()):
            load = loads.get(subentry_id)
            if load is None:
                self._shed_monitored_loads.pop(subentry_id, None)
                self._manual_restore_notified.discard(subentry_id)
                continue

            record["resume_entity"] = str(
                load.get(CONF_MONITORED_LOAD_RESUME_ENTITY) or ""
            )
            entity_id = str(record.get("emergency_entity") or "")
            domain = entity_id.split(".", 1)[0] if entity_id else ""
            if domain in {"switch", "input_boolean", "climate", "fan", "light"}:
                state = self.hass.states.get(entity_id)
                if (
                    state is not None
                    and state.state not in (STATE_OFF, STATE_UNKNOWN, STATE_UNAVAILABLE)
                    and not self._electrical_warning_active(data)
                ):
                    # The user has explicitly restored a stateful load before the
                    # automatic recovery timer. Do not send a duplicate resume.
                    self._shed_monitored_loads.pop(subentry_id, None)
                    self._manual_restore_notified.discard(subentry_id)

    def _write_emergency_diagnostics(self, data: dict[str, Any], now: Any) -> None:
        safe_elapsed = None
        if self._electrical_safe_since is not None:
            safe_elapsed = max((now - self._electrical_safe_since).total_seconds(), 0.0)
        data["monitored_emergency_control"] = {
            "enabled_with_real_control": self.real_control_enabled,
            "policy": {
                "grid_total": "monitored_emergency_first",
                "phase_or_inverter": "managed_first_then_monitored",
                "battery_or_pv_optimization": "never_controls_monitored_loads",
                "recovery_stable_seconds": MONITORED_EMERGENCY_RECOVERY_STABLE_SECONDS,
                "active_power_threshold_w": MONITORED_EMERGENCY_ACTIVE_POWER_THRESHOLD_W,
            },
            "electrical_warning_active": self._electrical_warning_active(data),
            "safe_since": (
                self._electrical_safe_since.isoformat()
                if self._electrical_safe_since is not None
                else None
            ),
            "safe_elapsed_seconds": (
                round(safe_elapsed, 1) if safe_elapsed is not None else None
            ),
            "shed_loads": [
                {
                    "subentry_id": subentry_id,
                    "name": record.get("name"),
                    "phase": record.get("phase"),
                    "emergency_entity": record.get("emergency_entity"),
                    "resume_entity": record.get("resume_entity") or None,
                    "shed_at": record.get("shed_at"),
                    "reason": record.get("reason"),
                    "manual_restore_required": not bool(record.get("resume_entity")),
                }
                for subentry_id, record in self._shed_monitored_loads.items()
            ],
            "manual_restore_pending": [
                subentry_id
                for subentry_id, record in self._shed_monitored_loads.items()
                if not record.get("resume_entity")
            ],
        }

    async def _async_notify_manual_restore(
        self, subentry_id: str, record: dict[str, Any]
    ) -> None:
        if subentry_id in self._manual_restore_notified:
            return
        if not self.hass.services.has_service("persistent_notification", "create"):
            return
        name = str(record.get("name") or "Carico monitorato")
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Casa ES — ripristino manuale",
                "message": (
                    f"{name} è stato disattivato per protezione elettrica. "
                    "La situazione è tornata stabile: puoi riattivarlo manualmente."
                ),
                "notification_id": f"casa_es_emergency_{subentry_id}",
            },
            blocking=False,
        )
        self._manual_restore_notified.add(subentry_id)

    async def _async_execute_monitored_shed(
        self,
        load: dict[str, Any],
        *,
        reason: str,
        now: Any,
        data: dict[str, Any],
    ) -> bool:
        subentry_id = str(load.get("subentry_id") or "")
        entity_id = str(load.get(CONF_MONITORED_LOAD_EMERGENCY_ENTITY) or "")
        if not subentry_id or not entity_id or self._command_recent(entity_id, now):
            return False
        try:
            action = await self._async_call_monitored_control(entity_id, resume=False)
        except Exception as err:
            self._record_real_command(
                entity_id=entity_id,
                action="emergency_control",
                reason=reason,
                now=now,
                error=str(err),
            )
            self._write_emergency_diagnostics(data, now)
            self._write_real_control_diagnostics(data, "error")
            return True

        self._shed_monitored_loads[subentry_id] = {
            "name": load.get(CONF_MONITORED_LOAD_NAME) or load.get("name"),
            "phase": load.get(CONF_MONITORED_LOAD_PHASE) or load.get("phase"),
            "emergency_entity": entity_id,
            "resume_entity": str(load.get(CONF_MONITORED_LOAD_RESUME_ENTITY) or ""),
            "shed_at": now.isoformat(),
            "reason": reason,
        }
        self._manual_restore_notified.discard(subentry_id)
        self._record_real_command(
            entity_id=entity_id,
            action=action,
            reason=reason,
            now=now,
            error=None,
        )
        self._write_emergency_diagnostics(data, now)
        self._write_real_control_diagnostics(data, "command_sent")
        return True

    async def _async_recover_one_monitored_load(
        self, data: dict[str, Any], now: Any
    ) -> bool:
        if not self._shed_monitored_loads:
            self._electrical_safe_since = None
            return False

        if self._electrical_safe_since is None:
            self._electrical_safe_since = now
            return False
        elapsed = max((now - self._electrical_safe_since).total_seconds(), 0.0)
        if elapsed < MONITORED_EMERGENCY_RECOVERY_STABLE_SECONDS:
            return False

        current_loads = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("monitored_loads") or [])
        }
        for subentry_id, record in list(self._shed_monitored_loads.items()):
            resume_entity = str(record.get("resume_entity") or "")
            if not resume_entity:
                try:
                    await self._async_notify_manual_restore(subentry_id, record)
                except Exception:
                    pass
                continue

            load = current_loads.get(subentry_id) or {}
            try:
                power_w = float(load.get("current_power_w") or 0.0)
            except (TypeError, ValueError):
                power_w = 0.0
            if power_w > MONITORED_EMERGENCY_ACTIVE_POWER_THRESHOLD_W:
                # It has already been resumed manually or by its own integration.
                self._shed_monitored_loads.pop(subentry_id, None)
                self._manual_restore_notified.discard(subentry_id)
                continue

            if self._command_recent(resume_entity, now):
                return True
            reason = (
                "Ripristino dopo almeno 2 minuti senza allarmi rete, fase o inverter."
            )
            try:
                action = await self._async_call_monitored_control(
                    resume_entity, resume=True
                )
            except Exception as err:
                self._record_real_command(
                    entity_id=resume_entity,
                    action="emergency_resume",
                    reason=reason,
                    now=now,
                    error=str(err),
                )
                self._write_emergency_diagnostics(data, now)
                self._write_real_control_diagnostics(data, "error")
                return True

            self._shed_monitored_loads.pop(subentry_id, None)
            self._manual_restore_notified.discard(subentry_id)
            self._record_real_command(
                entity_id=resume_entity,
                action=action,
                reason=reason,
                now=now,
                error=None,
            )
            self._write_emergency_diagnostics(data, now)
            self._write_real_control_diagnostics(data, "command_sent")
            return True
        return False

    def _managed_hard_candidates(
        self, data: dict[str, Any], now: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        decisions = list(data.get("dry_run_decisions") or [])
        candidates = [
            item
            for item in decisions
            if item.get("would_stop")
            and item.get("stop_is_hard_safety")
            and item.get("management_mode") != DEVICE_MODE_OVERRIDE
            and item.get("entity_active")
        ]

        # A phase overload must be solved on that physical phase. If an inverter
        # total warning is also active, any managed load can contribute.
        if data.get("phase_warning") and not data.get("inverter_warning"):
            phases = warning_phases(data)
            if phases:
                candidates = [
                    item
                    for item in candidates
                    if str(item.get("phase") or "") in phases
                    or str(item.get("phase") or "") == "three_phase"
                ]

        candidates.sort(key=lambda item: -int(item.get("priority") or 50))
        selected = None
        for item in candidates:
            entity_id = str(item.get("entity_id") or "")
            if entity_id and not self._command_recent(entity_id, now):
                selected = item
                break
        return candidates, selected

    async def _async_execute_managed_stop(
        self,
        decision: dict[str, Any],
        *,
        reason: str | None,
        now: Any,
        data: dict[str, Any],
    ) -> bool:
        entity_id = str(decision.get("entity_id") or "")
        if not entity_id:
            return False
        final_reason = str(reason or decision.get("reason") or "Protezione Casa ES")
        try:
            await self._async_call_entity_control(entity_id, False)
        except Exception as err:
            self._record_real_command(
                entity_id=entity_id,
                action="turn_off",
                reason=final_reason,
                now=now,
                error=str(err),
            )
            self._write_emergency_diagnostics(data, now)
            self._write_real_control_diagnostics(data, "error")
            return True

        self._record_real_command(
            entity_id=entity_id,
            action="turn_off",
            reason=final_reason,
            now=now,
            error=None,
        )
        self._write_emergency_diagnostics(data, now)
        self._write_real_control_diagnostics(data, "command_sent")
        return True

    def _grid_normal_stop_candidate(
        self, data: dict[str, Any], now: Any
    ) -> dict[str, Any] | None:
        """After emergency shedding, respect normal minimum-ON semantics."""
        configs = {
            str(item.get("subentry_id") or ""): item
            for item in (data.get("managed_device_configs") or [])
        }
        candidates: list[dict[str, Any]] = []
        for decision in data.get("dry_run_decisions") or []:
            mode = str(decision.get("management_mode") or DEVICE_MODE_AUTO)
            if mode == DEVICE_MODE_OVERRIDE or not decision.get("entity_active"):
                continue
            source = configs.get(str(decision.get("subentry_id") or ""), {})
            if mode == DEVICE_MODE_OFF:
                candidates.append(decision)
                continue
            if mode != DEVICE_MODE_AUTO:
                continue
            if bool(source.get(CONF_DEVICE_ON_ONLY, False)):
                continue
            if not decision.get("can_auto_stop"):
                continue
            candidates.append(decision)

        candidates.sort(key=lambda item: -int(item.get("priority") or 50))
        for decision in candidates:
            entity_id = str(decision.get("entity_id") or "")
            if entity_id and not self._command_recent(entity_id, now):
                return decision
        return None

    async def _async_apply_real_control(self, data: dict[str, Any], now: Any) -> None:
        """Apply v1.4 ordering while preserving the v1.2 master safety switch."""
        self._refresh_shed_records(data)
        warning_active = self._electrical_warning_active(data)
        if warning_active:
            self._electrical_safe_since = None

        if not self.real_control_enabled:
            self._write_emergency_diagnostics(data, now)
            self._write_real_control_diagnostics(data, "disabled")
            return

        safe_grid, safe_inverter, safe_phase = self._safe_limits()
        monitored = list(data.get("monitored_loads") or [])
        excluded = set(self._shed_monitored_loads)

        # 1) True total-grid emergency: monitored emergency-capable loads are the
        # first sacrificial layer. Pick only enough power to solve the measured
        # excess, then re-measure on the next 5-second refresh.
        if data.get("grid_warning"):
            candidates = eligible_emergency_loads(
                monitored, excluded_subentry_ids=excluded
            )
            candidate = choose_relief_candidate(
                candidates, grid_relief_w(data, safe_grid)
            )
            if candidate is not None:
                reason = (
                    "Protezione rete: riduzione rapida del prelievo totale tramite "
                    "carico monitorato con comando di emergenza."
                )
                if await self._async_execute_monitored_shed(
                    candidate, reason=reason, now=now, data=data
                ):
                    return

        # 2) Phase/inverter overload: managed flexible loads are tried first.
        # This deliberately retains the existing hard electrical behavior for a
        # real inverter/phase risk; the v1.4 change to the 20-minute behavior is
        # only that a total-grid event no longer preempts them before monitored
        # emergency loads have been tried.
        if data.get("phase_warning") or data.get("inverter_warning"):
            managed_candidates, managed_selected = self._managed_hard_candidates(
                data, now
            )
            if managed_selected is not None:
                if await self._async_execute_managed_stop(
                    managed_selected, reason=None, now=now, data=data
                ):
                    return
            if managed_candidates:
                # A managed hard-safety candidate exists but is inside command
                # cooldown. Wait for the next measurement instead of sacrificing
                # a monitored appliance prematurely.
                self._write_emergency_diagnostics(data, now)
                self._write_real_control_diagnostics(data, "enabled")
                return

            # No useful managed load remains: only now fall back to emergency-
            # capable monitored loads on the affected phase, then inverter total.
            if data.get("phase_warning"):
                phases = warning_phases(data)
                phase, required = most_overloaded_phase(data, phases, safe_phase)
                if phase is not None:
                    phase_candidates = eligible_emergency_loads(
                        monitored,
                        phases={phase},
                        excluded_subentry_ids=excluded,
                    )
                    candidate = choose_relief_candidate(phase_candidates, required)
                    if candidate is not None:
                        reason = (
                            f"Protezione fase {phase.upper()}: i carichi gestiti non "
                            "sono sufficienti, uso un carico monitorato di emergenza."
                        )
                        if await self._async_execute_monitored_shed(
                            candidate, reason=reason, now=now, data=data
                        ):
                            return

            if data.get("inverter_warning"):
                candidates = eligible_emergency_loads(
                    monitored, excluded_subentry_ids=excluded
                )
                candidate = choose_relief_candidate(
                    candidates, inverter_relief_w(data, safe_inverter)
                )
                if candidate is not None:
                    reason = (
                        "Protezione inverter: i carichi gestiti non sono sufficienti, "
                        "uso un carico monitorato di emergenza."
                    )
                    if await self._async_execute_monitored_shed(
                        candidate, reason=reason, now=now, data=data
                    ):
                        return

            self._write_emergency_diagnostics(data, now)
            self._write_real_control_diagnostics(data, "enabled")
            return

        # 3) Grid-only warning after monitored shedding: managed loads may stop
        # only with their normal minimum-ON/non-interruptible rules. No v1.2 hard
        # grid preemption is used here.
        if data.get("grid_warning"):
            selected = self._grid_normal_stop_candidate(data, now)
            if selected is not None:
                reason = (
                    "Prelievo rete ancora elevato dopo lo sgancio dei carichi "
                    "monitorati; arresto del carico gestito con tempo minimo ON rispettato."
                )
                if await self._async_execute_managed_stop(
                    selected, reason=reason, now=now, data=data
                ):
                    return
            self._write_emergency_diagnostics(data, now)
            self._write_real_control_diagnostics(data, "enabled")
            return

        # 4) Once every electrical warning has been absent for two minutes, resume
        # only loads explicitly configured with a resume command, one per refresh.
        if await self._async_recover_one_monitored_load(data, now):
            return

        self._write_emergency_diagnostics(data, now)
        await super()._async_apply_real_control(data, now)

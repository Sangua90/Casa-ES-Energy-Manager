"""Casa ES Energy Manager v1.5.7 select pause/resume support."""

from __future__ import annotations

import unicodedata
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .coordinator_v156 import CasaESEnergyCoordinator as V156Coordinator

PAUSE_ALIASES = {"pause", "paused", "pausa", "in pausa"}
RESUME_ALIASES = {
    "resume",
    "riprendi",
    "run",
    "running",
    "start",
    "avvia",
    "continua",
    "continue",
    "play",
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.strip().lower().replace("_", " ").replace("-", " ").split())


def _match_option(options: list[Any], aliases: set[str]) -> str | None:
    normalized = [(_norm(option), str(option)) for option in options]
    for norm, raw in normalized:
        if norm in aliases:
            return raw
    for norm, raw in normalized:
        if any(alias in norm for alias in aliases if len(alias) >= 4):
            return raw
    return None


class CasaESEnergyCoordinator(V156Coordinator):
    """v1.5.7 adds safe select-based pause/resume to monitored loads."""

    async def _async_call_monitored_control(
        self, entity_id: str, *, resume: bool
    ) -> str:
        domain = entity_id.split(".", 1)[0]
        if domain != "select":
            return await super()._async_call_monitored_control(entity_id, resume=resume)

        state = self.hass.states.get(entity_id)
        if state is None:
            raise HomeAssistantError(f"Entità select non disponibile: {entity_id}")
        options = list(state.attributes.get("options") or [])
        aliases = RESUME_ALIASES if resume else PAUSE_ALIASES
        option = _match_option(options, aliases)
        if option is None:
            action = "ripresa" if resume else "pausa"
            raise HomeAssistantError(
                f"Nessuna opzione riconoscibile di {action} su {entity_id}: {options}"
            )
        if not self.hass.services.has_service("select", "select_option"):
            raise HomeAssistantError("Servizio select.select_option non disponibile")

        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": entity_id, "option": option},
            blocking=True,
        )
        return f"select.select_option:{option}"

    async def _async_update_data(self) -> dict[str, Any]:
        data = await super()._async_update_data()
        data["v157_select_pause_resume_supported"] = True
        data["v157_select_pause_aliases"] = sorted(PAUSE_ALIASES)
        data["v157_select_resume_aliases"] = sorted(RESUME_ALIASES)
        return data

"""Pure command semantics for emergency-capable monitored loads."""

from __future__ import annotations


def effective_resume_entity(
    emergency_entity: str | None, resume_entity: str | None
) -> tuple[str, str]:
    """Return the effective resume entity and how it was resolved.

    An explicit resume command always wins. If the emergency command itself is
    a switch, the same switch safely provides the inverse operation: turn_off
    during electrical shedding and turn_on during recovery. Other domains keep
    the existing explicit-resume/manual-recovery behavior.
    """
    explicit = str(resume_entity or "").strip()
    if explicit:
        return explicit, "explicit"

    emergency = str(emergency_entity or "").strip()
    if emergency.startswith("switch."):
        return emergency, "same_switch"

    return "", "manual"

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    enabled: bool
    reason: str
    activated_timestamp: datetime | None
    activated_by: str


def kill_switch_failure(state: object) -> str | None:
    if not isinstance(state, KillSwitchState):
        return "Kill-switch state is missing or malformed."
    if not isinstance(state.enabled, bool):
        return "Kill-switch enabled state is malformed."
    if state.enabled:
        if not state.reason.strip() or not state.activated_by.strip():
            return "Kill switch is enabled with incomplete activation metadata."
        if state.activated_timestamp is None or state.activated_timestamp.tzinfo is None:
            return "Kill switch is enabled with an invalid activation timestamp."
        return f"Kill switch is enabled: {state.reason}"
    return None

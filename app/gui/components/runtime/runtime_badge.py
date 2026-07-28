from __future__ import annotations

from app.gui.components.common import StatusPill


class RuntimeBadge(StatusPill):
    LEVELS_BY_STATE = {
        "RUNNING": "good",
        "STARTING": "warn",
        "STOPPING": "warn",
        "STOPPED": "danger",
        "FAILED": "danger",
    }

    def __init__(self, state: str = "STOPPED") -> None:
        super().__init__()
        self.setObjectName("runtimeBadge")
        self.set_state(state)

    def set_state(self, state: str) -> None:
        normalized = str(state).upper()
        self.set_status(
            normalized,
            self.LEVELS_BY_STATE.get(normalized, "neutral"),
        )


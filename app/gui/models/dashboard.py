from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .runtime import RuntimeState


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    occurred_at: datetime
    message: str


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    environment: str
    runtime_state: RuntimeState
    broker_status: str
    market_feed_status: str
    inference_status: str
    emergency_stop_enabled: bool
    active_model: str
    cycle_count: int
    status_message: str
    activity: tuple[ActivityEntry, ...]

    @classmethod
    def initial(cls) -> "DashboardSnapshot":
        return cls(
            environment="PAPER",
            runtime_state=RuntimeState.STOPPED,
            broker_status="Disconnected",
            market_feed_status="Idle",
            inference_status="Ready",
            emergency_stop_enabled=True,
            active_model="Not loaded",
            cycle_count=0,
            status_message="Ready to start.",
            activity=(),
        )

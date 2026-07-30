from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class HealthState:
    runtime_status: str | None = None
    broker_status: str | None = None
    market_data_status: str | None = None
    ai_status: str | None = None
    risk_status: str | None = None
    persistence_status: str | None = None
    last_error: str | None = None
    last_warning: str | None = None
    last_heartbeat: datetime | None = None
    connection_latency: str | None = None
    reconnect_attempts: int = 0
    degraded: bool = False
    healthy: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "runtime_status",
            "broker_status",
            "market_data_status",
            "ai_status",
            "risk_status",
            "persistence_status",
            "last_error",
            "last_warning",
            "connection_latency",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"health {field_name} must be None or stripped text"
                )
        if (
            self.last_heartbeat is not None
            and self.last_heartbeat.tzinfo is None
        ):
            raise ValueError("last_heartbeat must be timezone-aware")
        if (
            isinstance(self.reconnect_attempts, bool)
            or not isinstance(self.reconnect_attempts, int)
            or self.reconnect_attempts < 0
        ):
            raise ValueError("reconnect_attempts must be nonnegative")
        if not isinstance(self.degraded, bool):
            raise TypeError("degraded must be a bool")
        if not isinstance(self.healthy, bool):
            raise TypeError("healthy must be a bool")
        if self.healthy and self.degraded:
            raise ValueError("health cannot be healthy and degraded")

    @classmethod
    def initial(cls) -> "HealthState":
        return cls()

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime

@dataclass(frozen=True, slots=True)
class ConnectionHealth:
    connected: bool = False
    authenticated: bool = False
    websocket_connected: bool = False
    latency_microseconds: int | None = None
    reconnect_count: int = 0
    last_successful_heartbeat: datetime | None = None

def update_health(health: ConnectionHealth, **changes) -> ConnectionHealth:
    if "last_successful_heartbeat" in changes and changes["last_successful_heartbeat"] is not None and changes["last_successful_heartbeat"].tzinfo is None: raise ValueError("heartbeat timestamp must be timezone-aware")
    if "latency_microseconds" in changes and changes["latency_microseconds"] is not None and changes["latency_microseconds"] < 0: raise ValueError("latency must be nonnegative")
    return replace(health, **changes)

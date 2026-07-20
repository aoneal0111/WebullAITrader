from __future__ import annotations
from typing import Protocol
from app.market_data.events import append_event
from app.market_data.models import HeartbeatPayload, MarketEventLog, MarketEventType
from app.webull.errors import NetworkError, SerializationError
from app.webull.health import ConnectionHealth, update_health

class StreamBackend(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def subscribe(self, channels: tuple[str, ...]) -> None: ...
    def receive(self) -> object | None: ...

class OfficialSdkStreamBackend:
    """Adapts an official Webull SDK MQTT/gRPC client without exposing it upstream."""
    def __init__(self, sdk_client): self.client = sdk_client
    def connect(self): return self.client.connect()
    def disconnect(self): return self.client.disconnect()
    def subscribe(self, channels): return self.client.subscribe(channels)
    def receive(self): return self.client.receive()

class WebullWebSocketClient:
    """Protocol adapter for the official SDK's MQTT/gRPC streaming clients."""
    def __init__(self, backend, parser, reconnect_policy, sleeper, logger):
        self.backend, self.parser, self.policy, self.sleeper, self.logger = backend, parser, reconnect_policy, sleeper, logger
        self.health = ConnectionHealth(); self.log = MarketEventLog(); self.channels = ()
    def connect(self):
        try:
            self.backend.connect(); self.health = update_health(self.health, websocket_connected=True, connected=True); self.logger.log("stream_connect", "succeeded")
        except Exception as exc:
            self.logger.log("stream_connect", "failed", error_type=type(exc).__name__); raise NetworkError("Webull stream connection failed", retryable=True) from exc
    def disconnect(self): self.backend.disconnect(); self.health = update_health(self.health, websocket_connected=False); self.logger.log("stream_disconnect", "succeeded")
    def subscribe(self, channels):
        self.channels = tuple(sorted(set(channels))); self.backend.subscribe(self.channels); self.logger.log("stream_subscribe", "succeeded", channel_count=len(self.channels))
    def receive(self):
        for attempt in range(self.policy.maximum_attempts + 1):
            try:
                message = self.backend.receive()
                if message is None: return None
                event = self.parser(message)
                before = len(self.log.events)
                try: self.log = append_event(self.log, event)
                except ValueError as exc:
                    if any(item.source == event.source and item.sequence == event.sequence for item in self.log.events): return None
                    raise SerializationError("invalid Webull stream sequence") from exc
                if event.event_type is MarketEventType.HEARTBEAT and isinstance(event.payload, HeartbeatPayload):
                    self.health = update_health(self.health, last_successful_heartbeat=event.timestamp)
                self.logger.log("stream_receive", "succeeded", event_type=event.event_type.value); return event
            except SerializationError: raise
            except Exception as exc:
                if attempt >= self.policy.maximum_attempts: raise NetworkError("Webull stream reconnect exhausted", retryable=False) from exc
                self.sleeper(self.policy.backoff_seconds); self.backend.connect(); self.backend.subscribe(self.channels)
                self.health = update_health(self.health, websocket_connected=True, reconnect_count=self.health.reconnect_count + 1)
                self.logger.log("stream_reconnect", "succeeded", reconnect_count=self.health.reconnect_count)

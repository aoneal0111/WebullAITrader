from __future__ import annotations

from queue import Empty, Queue
from typing import Callable, Protocol

from app.market_data.events import append_event
from app.market_data.models import HeartbeatPayload, MarketEventLog, MarketEventType
from app.webull.errors import NetworkError, SerializationError
from app.webull.health import ConnectionHealth, update_health


class StreamBackend(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def subscribe(self, channels: tuple[str, ...]) -> None: ...
    def receive(self) -> object | None: ...


SubscriptionMapper = Callable[[tuple[str, ...]], object]


class OfficialSdkStreamBackend:
    """Adapt the official callback-driven Webull SDK client to a receive API.

    Webull's ``DataStreamingClient`` inherits from the Paho MQTT client. Incoming
    messages therefore arrive through ``on_message`` rather than a synchronous
    ``receive`` method. This adapter captures those callbacks in a thread-safe
    queue while keeping SDK-specific subscription arguments behind an injected
    mapper.
    """

    def __init__(
        self,
        sdk_client: object,
        *,
        subscription_mapper: SubscriptionMapper | None = None,
        receive_timeout_seconds: float = 1.0,
    ) -> None:
        if receive_timeout_seconds < 0:
            raise ValueError("receive_timeout_seconds must be non-negative")

        self.client = sdk_client
        self._subscription_mapper = subscription_mapper
        self._receive_timeout_seconds = receive_timeout_seconds
        self._messages: Queue[object] = Queue()
        self._original_on_message = getattr(sdk_client, "on_message", None)
        setattr(sdk_client, "on_message", self._on_message)

    def _on_message(self, client: object, userdata: object, message: object) -> None:
        payload = getattr(message, "payload", message)
        self._messages.put(payload)

        if callable(self._original_on_message):
            self._original_on_message(client, userdata, message)

    def connect(self) -> None:
        connect_and_loop_start = getattr(self.client, "connect_and_loop_start", None)
        if callable(connect_and_loop_start):
            connect_and_loop_start()
            return

        connect = getattr(self.client, "connect", None)
        if not callable(connect):
            raise TypeError("official SDK streaming client has no connect method")
        connect()

    def disconnect(self) -> None:
        loop_stop = getattr(self.client, "loop_stop", None)
        if callable(loop_stop):
            loop_stop()

        disconnect = getattr(self.client, "disconnect", None)
        if not callable(disconnect):
            raise TypeError("official SDK streaming client has no disconnect method")
        disconnect()

    def subscribe(self, channels: tuple[str, ...]) -> None:
        subscribe = getattr(self.client, "subscribe", None)
        if not callable(subscribe):
            raise TypeError("official SDK streaming client has no subscribe method")

        if self._subscription_mapper is None:
            subscribe(channels)
            return

        mapped = self._subscription_mapper(channels)
        if isinstance(mapped, dict):
            subscribe(**mapped)
            return
        if isinstance(mapped, tuple):
            subscribe(*mapped)
            return
        raise TypeError("subscription_mapper must return a tuple or dict")

    def receive(self) -> object | None:
        try:
            return self._messages.get(timeout=self._receive_timeout_seconds)
        except Empty:
            return None


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

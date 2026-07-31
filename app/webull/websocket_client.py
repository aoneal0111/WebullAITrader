from __future__ import annotations

from queue import Empty, Queue
from threading import Event
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
StreamLifecycleSink = Callable[
    [str, int, Exception | None],
    None,
]


class OfficialSdkStreamBackend:
    """Adapt Webull's callback-driven streaming client to a receive API.

    The official SDK publishes decoded quote objects through
    ``on_quotes_message(client, topic, quotes)``. Atlas stores those objects in a
    thread-safe queue and exposes a synchronous ``receive`` boundary to the rest
    of the runtime.
    """

    def __init__(
        self,
        sdk_client: object,
        *,
        subscription_mapper: SubscriptionMapper | None = None,
        receive_timeout_seconds: float = 1.0,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        if receive_timeout_seconds < 0:
            raise ValueError("receive_timeout_seconds must be non-negative")
        if connect_timeout_seconds < 0:
            raise ValueError("connect_timeout_seconds must be non-negative")

        self.client = sdk_client
        self._subscription_mapper = subscription_mapper
        self._receive_timeout_seconds = receive_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._messages: Queue[object] = Queue()
        self._connected = Event()
        self._subscription_acknowledged = Event()
        self._lifecycle_sink: StreamLifecycleSink | None = None
        self._deliberate_shutdown = False
        self._consumption_started = False

        self._original_on_quotes_message = getattr(sdk_client, "on_quotes_message", None)
        self._original_on_connect_success = getattr(sdk_client, "on_connect_success", None)
        self._original_on_disconnect = getattr(sdk_client, "on_disconnect", None)

        setattr(sdk_client, "on_quotes_message", self._on_quotes_message)
        setattr(sdk_client, "on_connect_success", self._on_connect_success)
        if hasattr(sdk_client, "on_disconnect"):
            setattr(sdk_client, "on_disconnect", self._on_disconnect)

    def _on_quotes_message(self, client: object, topic: object, quotes: object) -> None:
        self._messages.put((topic, quotes))
        if callable(self._original_on_quotes_message):
            self._original_on_quotes_message(client, topic, quotes)

    def _on_connect_success(self, client: object, api_client: object, session_id: object) -> None:
        self._connected.set()
        if callable(self._original_on_connect_success):
            self._original_on_connect_success(client, api_client, session_id)

    def _on_disconnect(self, *args: object, **kwargs: object) -> None:
        self._connected.clear()
        self._notify(
            "deliberate_shutdown"
            if self._deliberate_shutdown
            else "unexpected_stream_termination",
        )
        if callable(self._original_on_disconnect):
            self._original_on_disconnect(*args, **kwargs)

    def connect(self) -> None:
        self._connected.clear()
        self._subscription_acknowledged.clear()
        self._deliberate_shutdown = False
        self._consumption_started = False
        connect_and_loop_start = getattr(self.client, "connect_and_loop_start", None)
        if callable(connect_and_loop_start):
            try:
                connect_and_loop_start(logger_enable=False)
            except TypeError:
                connect_and_loop_start()
            if not self._connected.wait(timeout=self._connect_timeout_seconds):
                raise TimeoutError("official SDK streaming connection timed out")
            if self.actual_transport == "websockets":
                self._notify("websocket_http_upgrade")
            self._notify("mqtt_connack")
            return

        connect = getattr(self.client, "connect", None)
        if not callable(connect):
            raise TypeError("official SDK streaming client has no connect method")
        connect()

    def disconnect(self) -> None:
        self._deliberate_shutdown = True
        self._connected.clear()
        self._subscription_acknowledged.clear()
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

        self._notify("rest_subscription_requested")
        self._subscription_acknowledged.clear()
        if self._subscription_mapper is None:
            subscribe(channels)
            self._subscription_acknowledged.set()
            self._notify("rest_subscription_active")
            return

        mapped = self._subscription_mapper(channels)
        if isinstance(mapped, dict):
            subscribe(**mapped)
            self._subscription_acknowledged.set()
            self._notify("rest_subscription_active")
            return
        if isinstance(mapped, tuple):
            subscribe(*mapped)
            self._subscription_acknowledged.set()
            self._notify("rest_subscription_active")
            return
        raise TypeError("subscription_mapper must return a tuple or dict")

    @property
    def heartbeat_ok(self) -> bool:
        return self._connected.is_set()

    @property
    def subscription_acknowledged(self) -> bool:
        return self._subscription_acknowledged.is_set()

    def receive(self) -> object | None:
        try:
            message = self._messages.get(timeout=self._receive_timeout_seconds)
            if not self._consumption_started:
                self._consumption_started = True
                self._notify("active_event_consumption")
            return message
        except Empty:
            return None

    @property
    def actual_transport(self) -> str:
        return str(getattr(self.client, "_transport", "unknown"))

    def set_lifecycle_sink(
        self,
        sink: StreamLifecycleSink | None,
    ) -> None:
        if sink is not None and not callable(sink):
            raise TypeError("stream lifecycle sink must be callable")
        self._lifecycle_sink = sink

    def _notify(
        self,
        event: str,
        error: Exception | None = None,
    ) -> None:
        if self._lifecycle_sink is not None:
            self._lifecycle_sink(event, 0, error)


class WebullWebSocketClient:
    """Protocol adapter for the official SDK's MQTT/gRPC streaming clients."""

    def __init__(
        self,
        backend,
        parser,
        reconnect_policy,
        sleeper,
        logger,
        *,
        lifecycle_sink: StreamLifecycleSink | None = None,
    ):
        self.backend, self.parser, self.policy, self.sleeper, self.logger = backend, parser, reconnect_policy, sleeper, logger
        self.health = ConnectionHealth(); self.log = MarketEventLog(); self.channels = ()
        self.lifecycle_sink = lifecycle_sink

    def set_lifecycle_sink(
        self,
        sink: StreamLifecycleSink | None,
    ) -> None:
        if sink is not None and not callable(sink):
            raise TypeError("stream lifecycle sink must be callable")
        self.lifecycle_sink = sink
        backend_setter = getattr(self.backend, "set_lifecycle_sink", None)
        if callable(backend_setter):
            backend_setter(sink)

    def _notify(
        self,
        event: str,
        attempt: int,
        error: Exception | None = None,
    ) -> None:
        if self.lifecycle_sink is not None:
            self.lifecycle_sink(event, attempt, error)

    def connect(self):
        try:
            self.logger.log(
                "paho_transport_selected",
                "selected",
                transport=getattr(self.backend, "actual_transport", "unknown"),
            )
            self.backend.connect(); self.health = update_health(self.health, websocket_connected=True, connected=True); self.logger.log("stream_connect", "succeeded")
        except Exception as exc:
            self.logger.log("stream_connect", "failed", error_type=type(exc).__name__); raise NetworkError("Webull stream connection failed", retryable=True) from exc

    def disconnect(self):
        self.backend.disconnect()
        self.health = update_health(
            self.health,
            websocket_connected=False,
            connected=False,
        )
        self.logger.log("stream_disconnect", "deliberate")

    def subscribe(self, channels):
        self.channels = tuple(sorted(set(channels))); self.backend.subscribe(self.channels); self.logger.log("stream_subscribe", "succeeded", channel_count=len(self.channels))

    @property
    def heartbeat_ok(self):
        return bool(getattr(self.backend, "heartbeat_ok", self.health.connected))

    @property
    def subscription_acknowledged(self):
        return bool(getattr(self.backend, "subscription_acknowledged", False))

    @property
    def reconnect_ready(self):
        return self.policy.maximum_attempts > 0

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
            except SerializationError as exc:
                self._notify(
                    "parse_failed",
                    self.health.reconnect_count,
                    exc,
                )
                raise
            except Exception as exc:
                if attempt >= self.policy.maximum_attempts:
                    terminal = NetworkError(
                        "Webull stream reconnect exhausted",
                        retryable=False,
                    )
                    self._notify(
                        "terminal_failure",
                        self.health.reconnect_count,
                        terminal,
                    )
                    raise terminal from exc
                self._notify(
                    "reconnecting",
                    self.health.reconnect_count + 1,
                    exc,
                )
                self.sleeper(self.policy.backoff_seconds); self.backend.connect(); self.backend.subscribe(self.channels)
                self.health = update_health(self.health, websocket_connected=True, reconnect_count=self.health.reconnect_count + 1)
                self._notify(
                    "reconnected",
                    self.health.reconnect_count,
                )
                self.logger.log("stream_reconnect", "succeeded", reconnect_count=self.health.reconnect_count)

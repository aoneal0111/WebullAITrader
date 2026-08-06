from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from queue import Empty, Queue
from threading import Event, Lock
from time import sleep
from typing import Callable, Protocol

from app.market_data.events import append_event
from app.market_data.models import HeartbeatPayload, MarketEventLog, MarketEventType
from app.webull.errors import NetworkError, SerializationError
from app.webull.health import ConnectionHealth, update_health
from app.webull.market_event_parser import payload_metadata


class StreamBackend(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def subscribe(self, channels: tuple[str, ...]) -> None: ...
    def receive(self) -> object | None: ...


SubscriptionMapper = Callable[[tuple[str, ...]], object]
SdkClientFactory = Callable[[], object]
DiagnosticSink = Callable[[dict[str, object]], None]
StreamLifecycleSink = Callable[
    [str, int, Exception | None],
    None,
]


class _StreamSequenceError(SerializationError):
    """A normalized-event integrity error, not a wire payload parse failure."""


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
        registration_grace_seconds: float = 1.0,
        registration_retry_backoff_seconds: float = 1.0,
        maximum_registration_retries: int = 1,
        sleeper: Callable[[float], None] = sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sdk_client_factory: SdkClientFactory | None = None,
    ) -> None:
        if receive_timeout_seconds < 0:
            raise ValueError("receive_timeout_seconds must be non-negative")
        if connect_timeout_seconds < 0:
            raise ValueError("connect_timeout_seconds must be non-negative")
        if registration_grace_seconds < 0 or registration_retry_backoff_seconds < 0:
            raise ValueError("registration waits must be non-negative")
        if maximum_registration_retries < 0:
            raise ValueError("registration retries must be non-negative")

        self.client = sdk_client
        self._subscription_mapper = subscription_mapper
        self._receive_timeout_seconds = receive_timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._registration_grace_seconds = registration_grace_seconds
        self._registration_retry_backoff_seconds = registration_retry_backoff_seconds
        self._maximum_registration_retries = maximum_registration_retries
        self._sleeper = sleeper
        self._clock = clock
        self._sdk_client_factory = sdk_client_factory
        self._messages: Queue[object] = Queue()
        self._connected = Event()
        self._registration_ready = Event()
        self._identity_mismatch = Event()
        self._subscription_acknowledged = Event()
        self._lifecycle_sink: StreamLifecycleSink | None = None
        self._diagnostic_sink: DiagnosticSink | None = None
        self._deliberate_shutdown = False
        self._consumption_started = False
        self._has_connected = False
        self._active_subscription: tuple[str, ...] | None = None
        self._identity_lock = Lock()
        self._expected_session_id = ""
        self._owner_api_client: object | None = None
        self._original_on_quotes_message: object = None
        self._original_on_connect_success: object = None
        self._original_on_disconnect: object = None
        self._attach_client(sdk_client)

    @staticmethod
    def _hash(value: object) -> str:
        return sha256(str(value).encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _session_id(client: object) -> str:
        getter = getattr(client, "get_session_id", None)
        if callable(getter):
            return str(getter())
        value = (
            getattr(client, "quotes_session_id", None)
            or getattr(client, "_quotes_session_id", None)
            or getattr(client, "_client_id", "")
        )
        if not value and isinstance(getattr(client, "arguments", None), dict):
            value = client.arguments.get("session_id", "")
        return str(value)

    def _attach_client(self, client: object) -> None:
        session_id = self._session_id(client)
        if not session_id:
            raise ValueError("official SDK client has no streaming session identity")
        self.client = client
        self._expected_session_id = session_id
        self._owner_api_client = getattr(client, "api_client", None) or getattr(client, "_api_client", None)
        self._original_on_quotes_message = getattr(client, "on_quotes_message", None)
        self._original_on_connect_success = getattr(client, "on_connect_success", None)
        self._original_on_disconnect = getattr(client, "on_disconnect", None)
        setattr(client, "on_quotes_message", self._on_quotes_message)
        setattr(client, "on_connect_success", self._on_connect_success)
        if hasattr(client, "on_disconnect"):
            setattr(client, "on_disconnect", self._on_disconnect)
        self._emit_diagnostic("SESSION_IDENTITY_CREATED")

    def _replace_client(self) -> None:
        if self._sdk_client_factory is None:
            raise RuntimeError("stream reconnect requires a fresh SDK client identity")
        old_client = self.client
        for method_name in ("loop_stop", "disconnect"):
            method = getattr(old_client, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
        self._attach_client(self._sdk_client_factory())
        self._emit_diagnostic("STALE_SESSION_REPLACED")

    def _on_quotes_message(self, client: object, topic: object, quotes: object) -> None:
        if client is not self.client:
            self._emit_diagnostic("CROSS_CLIENT_MESSAGE_REJECTED")
            return
        self._messages.put((topic, quotes))
        if callable(self._original_on_quotes_message):
            self._original_on_quotes_message(client, topic, quotes)

    def _on_connect_success(self, client: object, api_client: object, session_id: object) -> None:
        if self._owner_api_client is None and client is self.client:
            self._owner_api_client = api_client
        if (
            client is not self.client
            or api_client is not self._owner_api_client
            or str(session_id) != self._expected_session_id
        ):
            self._emit_diagnostic("SESSION_IDENTITY_MISMATCH")
            self._identity_mismatch.set()
            return
        self._connected.set()
        self._emit_diagnostic(
            "MQTT_CONNECTED",
            mqtt_connected_at=self._clock().isoformat(),
        )
        if callable(self._original_on_connect_success):
            self._original_on_connect_success(client, api_client, session_id)

    def _on_disconnect(self, *args: object, **kwargs: object) -> None:
        self._connected.clear()
        self._registration_ready.clear()
        self._subscription_acknowledged.clear()
        self._active_subscription = None
        self._emit_diagnostic("SESSION_IDENTITY_INVALIDATED")
        self._expected_session_id = ""
        self._notify(
            "deliberate_shutdown"
            if self._deliberate_shutdown
            else "unexpected_stream_termination",
        )
        if callable(self._original_on_disconnect):
            self._original_on_disconnect(*args, **kwargs)

    def connect(self) -> None:
        if self._has_connected:
            self._replace_client()
        self._has_connected = True
        self._connected.clear()
        self._registration_ready.clear()
        self._identity_mismatch.clear()
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
                if not self._identity_mismatch.is_set() or self._sdk_client_factory is None:
                    raise TimeoutError("official SDK streaming connection timed out")
                self._replace_client()
                self._identity_mismatch.clear()
                connect_and_loop_start = getattr(self.client, "connect_and_loop_start", None)
                if not callable(connect_and_loop_start):
                    raise TypeError("replacement SDK client has no asynchronous connect method")
                try:
                    connect_and_loop_start(logger_enable=False)
                except TypeError:
                    connect_and_loop_start()
                if not self._connected.wait(timeout=self._connect_timeout_seconds):
                    raise TimeoutError("official SDK streaming connection timed out")
            if self.actual_transport == "websockets":
                self._notify("websocket_http_upgrade")
            self._notify("mqtt_connack")
            self._sleeper(self._registration_grace_seconds)
            if not self._connected.is_set():
                raise RuntimeError("MQTT disconnected before session registration became ready")
            self._registration_ready.set()
            self._emit_diagnostic(
                "REGISTRATION_READY",
                registration_ready_at=self._clock().isoformat(),
            )
            return

        connect = getattr(self.client, "connect", None)
        if not callable(connect):
            raise TypeError("official SDK streaming client has no connect method")
        connect()
        self._connected.set()
        self._sleeper(self._registration_grace_seconds)
        self._registration_ready.set()
        self._emit_diagnostic(
            "REGISTRATION_READY",
            registration_ready_at=self._clock().isoformat(),
        )

    def disconnect(self) -> None:
        self._deliberate_shutdown = True
        self._connected.clear()
        self._registration_ready.clear()
        self._subscription_acknowledged.clear()
        self._active_subscription = None
        self._emit_diagnostic("SESSION_IDENTITY_INVALIDATED")
        self._expected_session_id = ""
        loop_stop = getattr(self.client, "loop_stop", None)
        if callable(loop_stop):
            loop_stop()

        disconnect = getattr(self.client, "disconnect", None)
        if not callable(disconnect):
            raise TypeError("official SDK streaming client has no disconnect method")
        disconnect()

    def subscribe(self, channels: tuple[str, ...]) -> None:
        if not self._connected.is_set() or not self._registration_ready.is_set():
            raise RuntimeError("streaming session registration is not ready")
        normalized_channels = tuple(sorted(set(channels)))
        if self._subscription_acknowledged.is_set() and self._active_subscription == normalized_channels:
            self._emit_diagnostic("DUPLICATE_SUBSCRIPTION_SKIPPED")
            return
        subscribe = getattr(self.client, "subscribe", None)
        if not callable(subscribe):
            raise TypeError("official SDK streaming client has no subscribe method")

        self._subscription_acknowledged.clear()
        mapped = normalized_channels if self._subscription_mapper is None else self._subscription_mapper(normalized_channels)
        for retry_count in range(self._maximum_registration_retries + 1):
            self._notify("rest_subscription_requested")
            self._emit_diagnostic(
                "REGISTRATION_REQUEST_STARTED",
                retry_count=retry_count,
                **(
                    {"first_subscription_at": self._clock().isoformat()}
                    if retry_count == 0 else {}
                ),
            )
            try:
                if isinstance(mapped, dict):
                    subscribe(**mapped)
                elif isinstance(mapped, tuple):
                    subscribe(*mapped)
                else:
                    raise TypeError("subscription_mapper must return a tuple or dict")
            except Exception as exc:
                code = str(getattr(exc, "error_code", type(exc).__name__))
                request_id = getattr(exc, "request_id", None)
                self._emit_diagnostic(
                    "REGISTRATION_REQUEST_FAILED",
                    retry_count=retry_count,
                    rejection_code=code,
                    request_id=request_id,
                )
                if code != "INVALID_SESSION" or retry_count >= self._maximum_registration_retries:
                    raise
                self._registration_ready.clear()
                self._sleeper(self._registration_retry_backoff_seconds)
                if not self._connected.is_set():
                    raise RuntimeError("MQTT disconnected during session registration retry") from exc
                self._registration_ready.set()
                self._emit_diagnostic(
                    "REGISTRATION_READY",
                    retry_count=retry_count + 1,
                    registration_ready_at=self._clock().isoformat(),
                )
                continue
            self._subscription_acknowledged.set()
            self._active_subscription = normalized_channels
            self._notify("rest_subscription_active")
            self._emit_diagnostic(
                "REGISTRATION_REQUEST_SUCCEEDED",
                retry_count=retry_count,
            )
            return

    @property
    def heartbeat_ok(self) -> bool:
        return self._connected.is_set()

    @property
    def subscription_acknowledged(self) -> bool:
        return self._subscription_acknowledged.is_set()

    @property
    def registration_ready(self) -> bool:
        return self._registration_ready.is_set()

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

    def set_diagnostic_sink(self, sink: DiagnosticSink | None) -> None:
        if sink is not None and not callable(sink):
            raise TypeError("stream diagnostic sink must be callable")
        self._diagnostic_sink = sink
        if sink is not None:
            self._emit_diagnostic("SESSION_IDENTITY_CREATED")

    def _emit_diagnostic(self, phase: str, **fields: object) -> None:
        if self._diagnostic_sink is None:
            return
        record = {
            "session_lifecycle_phase": phase,
            "session_id_hash": self._hash(self._expected_session_id),
            "client_instance_hash": self._hash(f"{type(self.client).__name__}:{id(self.client)}"),
        }
        record.update({key: value for key, value in fields.items() if value is not None})
        self._diagnostic_sink(record)

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
        consecutive_decode_failure_threshold: int = 5,
    ):
        if consecutive_decode_failure_threshold <= 0:
            raise ValueError("decode failure threshold must be positive")
        self.backend, self.parser, self.policy, self.sleeper, self.logger = backend, parser, reconnect_policy, sleeper, logger
        self.health = ConnectionHealth(); self.log = MarketEventLog(); self.channels = ()
        self.lifecycle_sink = lifecycle_sink
        self.consecutive_decode_failure_threshold = consecutive_decode_failure_threshold
        self.consecutive_decode_failures = 0
        self.decoder_health = "STREAM_CONNECTED"
        diagnostic_setter = getattr(self.backend, "set_diagnostic_sink", None)
        if callable(diagnostic_setter):
            diagnostic_setter(
                lambda fields: self.logger.log("stream_session", "observed", **fields)
            )

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
            self.backend.connect(); self.health = update_health(self.health, websocket_connected=True, connected=True); self.decoder_health = "STREAM_CONNECTED"; self.logger.log("stream_connect", "succeeded")
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
        network_attempt = 0
        while network_attempt <= self.policy.maximum_attempts:
            try:
                message = self.backend.receive()
                if message is None: return None
                diagnostic = payload_metadata(message)
                classification = str(diagnostic["message_classification"])
                decoder = {
                    "QUOTE": "QuoteDecoder/QuoteResult",
                    "TRADE": "TickDecoder/TickResult",
                    "SNAPSHOT": "SnapshotDecoder/SnapshotResult",
                }.get(classification, "none")
                self.decoder_health = "STREAM_DECODING"
                self.logger.log(
                    "stream_payload", "classified", **diagnostic,
                    decoder_selected=decoder,
                    protobuf_message_type=(
                        type(message[1]).__name__
                        if isinstance(message, tuple) and len(message) == 2
                        else None
                    ),
                )
                event = self.parser(message)
                if event is None:
                    self.decoder_health = (
                        "STREAM_PAYLOAD_UNSUPPORTED"
                        if classification == "UNKNOWN"
                        else "STREAM_CONNECTED"
                    )
                    self.logger.log(
                        "stream_payload", "skipped", **diagnostic,
                        decoder_selected="none",
                    )
                    return None
                try: self.log = append_event(self.log, event)
                except ValueError as exc:
                    if any(item.source == event.source and item.sequence == event.sequence for item in self.log.events): return None
                    raise _StreamSequenceError("invalid Webull stream sequence") from exc
                recovered = self.consecutive_decode_failures > 0
                self.consecutive_decode_failures = 0
                self.decoder_health = "STREAM_CONNECTED"
                if recovered:
                    self._notify("decode_recovered", 0)
                if event.event_type is MarketEventType.HEARTBEAT and isinstance(event.payload, HeartbeatPayload):
                    self.health = update_health(self.health, last_successful_heartbeat=event.timestamp)
                self.logger.log(
                    "stream_receive", "succeeded",
                    event_type=event.event_type.value,
                    symbol=event.symbol,
                    sequence=event.sequence,
                    timestamp=event.timestamp.isoformat(),
                    decoder_health=self.decoder_health,
                ); return event
            except _StreamSequenceError:
                raise
            except SerializationError as exc:
                self.consecutive_decode_failures += 1
                threshold_reached = (
                    self.consecutive_decode_failures
                    >= self.consecutive_decode_failure_threshold
                )
                self.decoder_health = (
                    "STREAM_FAILED" if threshold_reached
                    else "STREAM_PARTIALLY_DEGRADED"
                )
                self.logger.log(
                    "stream_receive", "decode_failed",
                    error_type=type(exc).__name__,
                    consecutive_decode_failures=self.consecutive_decode_failures,
                    decoder_health=self.decoder_health,
                )
                self._notify(
                    "decode_threshold_exceeded" if threshold_reached else "parse_failed",
                    self.consecutive_decode_failures,
                    exc,
                )
                if threshold_reached:
                    raise
                continue
            except Exception as exc:
                if network_attempt >= self.policy.maximum_attempts:
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
                network_attempt += 1
                self.health = update_health(self.health, websocket_connected=True, reconnect_count=self.health.reconnect_count + 1)
                self._notify(
                    "reconnected",
                    self.health.reconnect_count,
                )
                self.logger.log("stream_reconnect", "succeeded", reconnect_count=self.health.reconnect_count)

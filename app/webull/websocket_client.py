from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from collections import deque
from queue import Empty, Full, Queue
from threading import Event, Lock
from time import monotonic, sleep
from typing import Callable, Protocol

from app.market_data.models import HeartbeatPayload, MarketEventLog, MarketEventType
from app.market_data.validation import validate_event
from app.performance_diagnostics import performance_diagnostics, subscription_fingerprint
from app.webull.errors import NetworkError, SerializationError
from app.webull.health import ConnectionHealth, update_health
from app.webull.market_event_parser import decoder_failure_metadata, payload_metadata


class StreamBackend(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def subscribe(self, channels: tuple[str, ...]) -> None: ...
    def receive(self) -> object | None: ...


SubscriptionMapper = Callable[[tuple[str, ...]], object]
SdkClientFactory = Callable[[], object]
GenerationAllocator = Callable[[], int]
DiagnosticSink = Callable[[dict[str, object]], None]
StreamLifecycleSink = Callable[
    [str, int, Exception | None],
    None,
]


class _StreamSequenceError(SerializationError):
    """A normalized-event integrity error, not a wire payload parse failure."""


@dataclass(frozen=True, slots=True)
class _ReceivedStreamPayload:
    topic: object
    payload: object
    received_timestamp: datetime
    generation: int


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
        generation_allocator: GenerationAllocator | None = None,
        ingestion_capacity: int = 4096,
    ) -> None:
        if receive_timeout_seconds < 0:
            raise ValueError("receive_timeout_seconds must be non-negative")
        if connect_timeout_seconds < 0:
            raise ValueError("connect_timeout_seconds must be non-negative")
        if registration_grace_seconds < 0 or registration_retry_backoff_seconds < 0:
            raise ValueError("registration waits must be non-negative")
        if maximum_registration_retries < 0:
            raise ValueError("registration retries must be non-negative")
        if ingestion_capacity <= 0:
            raise ValueError("ingestion capacity must be positive")

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
        self._generation_allocator = generation_allocator
        self._ingestion_capacity = int(ingestion_capacity)
        self._messages: Queue[object] = Queue(maxsize=self._ingestion_capacity)
        self._message_metrics_lock = Lock()
        self._message_queue_depth = 0
        self._messages_enqueued = 0
        self._messages_dequeued = 0
        self._message_queue_high_water = 0
        self._message_ingestion_overflow = 0
        self._callbacks_rejected_after_halt = 0
        self._messages_purged_on_disconnect = 0
        self._messages_discarded_retired_generation = 0
        self._halt_diagnostic_emitted = False
        self._startup_buffered_count = 0
        self._oldest_buffered_age_ms = 0.0
        self._oldest_buffered_age_high_water_ms = 0.0
        self._dequeue_residence_ms: deque[float] = deque(maxlen=2048)
        self._connected = Event()
        self._registration_ready = Event()
        self._identity_mismatch = Event()
        self._subscription_acknowledged = Event()
        self._lifecycle_sink: StreamLifecycleSink | None = None
        self._diagnostic_sink: DiagnosticSink | None = None
        self._deliberate_shutdown = False
        self._accepting_callbacks = Event()
        # Preserve the established startup-burst contract: callbacks may be
        # buffered before connect completes.  Terminal lifecycle code calls
        # halt_callback_ingestion() to close this gate permanently for that
        # session.
        self._accepting_callbacks.set()
        self._last_raw_callback_monotonic: float | None = None
        self._last_raw_callback_at: datetime | None = None
        self._generation = 0
        self._generation_started_at: datetime | None = None
        self._generation_first_raw_at: datetime | None = None
        self._generation_first_dequeued_at: datetime | None = None
        self._generation_callback_sequence = 0
        self._receive_loop_observed = False

        self._consumption_started = False
        self._has_connected = False
        self._active_subscription: tuple[str, ...] | None = None
        self._active_subscription_generation: int | None = None
        self._identity_lock = Lock()
        self._expected_session_id = ""
        self._owner_api_client: object | None = None
        self._original_on_quotes_message: object = None
        self._original_on_connect_success: object = None
        self._original_on_disconnect: object = None
        self._attach_client(sdk_client)

    def queue_depth(self) -> int:
        """Current callback FIFO depth for aggregate runtime diagnostics."""

        return self._messages.qsize()

    def memory_metrics(self) -> dict[str, int]:
        """Return primitive callback-FIFO cardinalities without retaining payloads."""

        with self._message_metrics_lock:
            return {
                "current_depth": self._message_queue_depth,
                "high_water_depth": self._message_queue_high_water,
                "messages_enqueued": self._messages_enqueued,
                "messages_dequeued": self._messages_dequeued,
                "startup_buffered_count": self._startup_buffered_count,
                "oldest_buffered_age_ms": self._oldest_buffered_age_ms,
                "oldest_buffered_age_high_water_ms": self._oldest_buffered_age_high_water_ms,
            }

    def latency_metrics(self) -> dict[str, float]:
        """Return bounded callback-residence percentiles separately from
        the stable queue-cardinality contract."""
        with self._message_metrics_lock:
            ordered = sorted(self._dequeue_residence_ms)
        return {
            "dequeue_residence_p50_ms": _percentile(ordered, 0.50),
            "dequeue_residence_p90_ms": _percentile(ordered, 0.90),
            "dequeue_residence_p95_ms": _percentile(ordered, 0.95),
            "dequeue_residence_p99_ms": _percentile(ordered, 0.99),
            "dequeue_residence_max_ms": max(ordered, default=0.0),
        }

    def ingestion_metrics(self) -> dict[str, int]:
        with self._message_metrics_lock:
            return {
                "ingestion_queue_capacity": self._ingestion_capacity,
                "ingestion_queue_overflow": self._message_ingestion_overflow,
                "ingestion_queue_depth": self._message_queue_depth,
                "ingestion_queue_high_water": self._message_queue_high_water,
                "callbacks_rejected_after_halt": self._callbacks_rejected_after_halt,
                "messages_purged_on_disconnect": self._messages_purged_on_disconnect,
                "messages_discarded_retired_generation": self._messages_discarded_retired_generation,
            }

    def discard_metrics(self) -> dict[str, int]:
        with self._message_metrics_lock:
            return {
                "messages_purged_on_disconnect": self._messages_purged_on_disconnect,
                "messages_discarded_retired_generation": self._messages_discarded_retired_generation,
                "callbacks_rejected_after_halt": self._callbacks_rejected_after_halt,
            }

    @property
    def generation_metrics(self) -> dict[str, object]:
        return {
            "generation": self._generation,
            "started_at": self._generation_started_at,
            "first_raw_callback_at": self._generation_first_raw_at,
            "first_callback_dequeued_at": self._generation_first_dequeued_at,
        }

    @property
    def session_identity_hash(self) -> str:
        return self._hash(self._expected_session_id)

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
            performance_diagnostics.record_stream_observability(
                "CALLBACK_REJECTED", reason_category="STALE_CLIENT", current_generation=self._generation
            )
            self._emit_diagnostic("CROSS_CLIENT_MESSAGE_REJECTED")
            return
        overflow = False
        with self._message_metrics_lock:
            self._generation_callback_sequence += 1
            if not self._accepting_callbacks.is_set():
                self._callbacks_rejected_after_halt += 1
                performance_diagnostics.record_stream_observability(
                    "CALLBACK_REJECTED", reason_category="INGESTION_HALTED",
                    generation=self._generation, callback_sequence=self._generation_callback_sequence,
                )
                if not self._halt_diagnostic_emitted:
                    self._halt_diagnostic_emitted = True
                    self._emit_diagnostic("CALLBACK_INGESTION_HALTED")
                return
            self._last_raw_callback_monotonic = monotonic()
            self._last_raw_callback_at = self._clock()
            if self._generation_first_raw_at is None:
                self._generation_first_raw_at = self._last_raw_callback_at
            message = _ReceivedStreamPayload(
                topic, quotes, self._clock(), self._generation
            )
            try:
                self._messages.put_nowait(message)
            except Full:
                self._message_ingestion_overflow += 1
                self._accepting_callbacks.clear()
                overflow = True
            if overflow:
                callback_timestamp = self._last_raw_callback_at
                generation = self._generation
            else:
                self._message_queue_depth += 1
                self._messages_enqueued += 1
                depth = self._message_queue_depth
                self._message_queue_high_water = max(
                    self._message_queue_high_water,
                    depth,
                )
                callback_timestamp = self._last_raw_callback_at
                generation = self._generation
        if overflow:
            performance_diagnostics.record_stream_observability(
                "CALLBACK_REJECTED", reason_category="INGESTION_OVERFLOW",
                generation=generation, callback_sequence=self._generation_callback_sequence,
            )
            self._emit_diagnostic(
                "CALLBACK_INGESTION_OVERFLOW",
                capacity=self._ingestion_capacity,
            )
            return
        performance_diagnostics.record_stream_raw_callback(
            timestamp=callback_timestamp,
            generation=generation,
        )
        if self._generation_callback_sequence == 1 or self._generation_callback_sequence % 1000 == 0:
            performance_diagnostics.record_stream_observability(
                "CALLBACK_ACCEPTED", generation=generation,
                callback_sequence=self._generation_callback_sequence,
                queue_depth=depth, current_generation=True,
            )
        performance_diagnostics.increment_startup_counter("raw_callbacks_received")
        performance_diagnostics.increment_startup_counter("callbacks_enqueued")
        performance_diagnostics.record_startup_stage("first_raw_callback")
        performance_diagnostics.record_market_event_callback(
            depth
        )
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
        performance_diagnostics.record_startup_stage("transport_connected")
        self._emit_diagnostic(
            "MQTT_CONNECTED",
            mqtt_connected_at=self._clock().isoformat(),
        )
        performance_diagnostics.record_stream_lifecycle(
            "connected",
            generation=self._generation,
            session_id_hash=self.session_identity_hash,
            timestamp=self._clock(),
        )
        if callable(self._original_on_connect_success):
            self._original_on_connect_success(client, api_client, session_id)

    def _on_disconnect(self, *args: object, **kwargs: object) -> None:
        self._connected.clear()
        self._registration_ready.clear()
        self._subscription_acknowledged.clear()
        self._active_subscription = None
        self._active_subscription_generation = None
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
        next_generation = (
            int(self._generation_allocator())
            if self._generation_allocator is not None
            else self._generation + 1
        )
        performance_diagnostics.record_stream_observability(
            "CONNECT_REQUESTED", generation=next_generation,
        )
        self._generation = next_generation
        performance_diagnostics.record_stream_observability(
            "CONNECT_STARTED", generation=self._generation,
        )
        self._generation_started_at = self._clock()
        self._generation_first_raw_at = None
        self._generation_first_dequeued_at = None
        self._generation_callback_sequence = 0
        self._receive_loop_observed = False
        self._startup_buffered_count = 0
        self._oldest_buffered_age_ms = 0.0
        self._oldest_buffered_age_high_water_ms = 0.0
        self._last_raw_callback_monotonic = None
        self._last_raw_callback_at = None
        performance_diagnostics.record_startup_stage("stream_connect_started")
        if self._has_connected:
            self._replace_client()
        self._has_connected = True
        self._connected.clear()
        self._registration_ready.clear()
        self._identity_mismatch.clear()
        self._subscription_acknowledged.clear()
        self._deliberate_shutdown = False
        self._accepting_callbacks.set()
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
            performance_diagnostics.record_stream_observability(
                "CONNECT_SUCCEEDED", generation=self._generation,
            )
            performance_diagnostics.record_startup_stage("registration_ready")
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
        performance_diagnostics.record_stream_observability(
            "CONNECT_SUCCEEDED", generation=self._generation,
        )
        performance_diagnostics.record_startup_stage("registration_ready")
        self._emit_diagnostic(
            "REGISTRATION_READY",
            registration_ready_at=self._clock().isoformat(),
        )

    def disconnect(self) -> None:
        retiring_generation = self._generation
        performance_diagnostics.record_stream_observability(
            "DISCONNECT_REQUESTED", generation=retiring_generation,
        )
        self._deliberate_shutdown = True
        self.halt_callback_ingestion()
        self._connected.clear()
        self._registration_ready.clear()
        self._subscription_acknowledged.clear()
        self._active_subscription = None
        self._active_subscription_generation = None
        self._emit_diagnostic("SESSION_IDENTITY_INVALIDATED")
        self._expected_session_id = ""
        loop_stop = getattr(self.client, "loop_stop", None)
        if callable(loop_stop):
            loop_stop()

        disconnect = getattr(self.client, "disconnect", None)
        if not callable(disconnect):
            raise TypeError("official SDK streaming client has no disconnect method")
        disconnect()
        performance_diagnostics.record_stream_boundary("transport_disconnected")
        performance_diagnostics.record_stream_observability(
            "GENERATION_RETIRED", generation=retiring_generation,
        )

    def halt_callback_ingestion(self) -> None:
        """Stop accepting callbacks after the sole consumer is gone.

        The FIFO is intentionally not given an arbitrary small capacity: a
        normal startup burst remains supported.  Once the consumer lifecycle
        is terminal, however, retaining producer payloads is unsafe, so the
        ingress gate closes and queued payloads are released.
        """
        was_accepting = self._accepting_callbacks.is_set()
        self._accepting_callbacks.clear()
        drained = 0
        while True:
            try:
                self._messages.get_nowait()
                drained += 1
            except Empty:
                break
        if drained:
            with self._message_metrics_lock:
                self._message_queue_depth = max(0, self._message_queue_depth - drained)
                self._messages_purged_on_disconnect += drained
        performance_diagnostics.record_stream_boundary("callback_ingestion_halted")
        if was_accepting and not self._halt_diagnostic_emitted:
            self._halt_diagnostic_emitted = True
            self._emit_diagnostic("CALLBACK_INGESTION_HALTED", drained=drained)

    def subscribe(self, channels: tuple[str, ...]) -> None:
        if not self._connected.is_set() or not self._registration_ready.is_set():
            raise RuntimeError("streaming session registration is not ready")
        normalized_channels = tuple(sorted(set(channels)))
        prior_channels = self._active_subscription or ()
        desired_fp = subscription_fingerprint(normalized_channels)
        prior_fp = subscription_fingerprint(prior_channels)
        subscription_operation = (
            "RECONNECT_REISSUE" if self._generation > 1 and not prior_channels
            else "STARTUP_SUBSCRIBE" if not prior_channels else "FULL_REPLACE"
        )
        if len(normalized_channels) > 100:
            raise ValueError(
                "Webull market-data subscriptions are limited to 100 symbols"
            )
        if (
            self._subscription_acknowledged.is_set()
            and self._active_subscription == normalized_channels
            and self._active_subscription_generation == self._generation
        ):
            performance_diagnostics.record_stream_observability(
                "SUBSCRIPTION_NO_CHANGE", generation=self._generation,
                desired_count=len(normalized_channels), desired_fingerprint=desired_fp,
                active_count=len(prior_channels), active_fingerprint=prior_fp,
                unchanged_count=len(normalized_channels), added_count=0, removed_count=0,
                operation="NO_CHANGE",
            )
            self._emit_diagnostic("DUPLICATE_SUBSCRIPTION_SKIPPED")
            return

        # The official SDK's subscribe endpoint is additive.  Replacing Atlas's
        # local tuple without first removing the prior server-side set leaks
        # tickers across universe rotations and eventually trips Webull's
        # TOO_MANY_SYMBOLS_SUBSCRIPTION terminal error.
        if self._active_subscription:
            unsubscribe_started = monotonic()
            performance_diagnostics.record_stream_observability(
                "UNSUBSCRIBE_REQUESTED", generation=self._generation,
                active_count=len(prior_channels), active_fingerprint=prior_fp,
                operation="FULL_REPLACE",
            )
            unsubscribe = getattr(self.client, "unsubscribe", None)
            if not callable(unsubscribe):
                raise TypeError(
                    "official SDK streaming client has no unsubscribe method"
                )
            unsubscribe(unsubscribe_all=True)
            self._active_subscription = None
            self._active_subscription_generation = None
            self._subscription_acknowledged.clear()
            self._emit_diagnostic("ACTIVE_SUBSCRIPTION_RELEASED")
            performance_diagnostics.record_stream_observability(
                "UNSUBSCRIBE_COMPLETED", generation=self._generation,
                active_count=0, active_fingerprint=subscription_fingerprint(()),
                operation="FULL_REPLACE",
                duration_ms=(monotonic() - unsubscribe_started) * 1000.0,
            )

        if not normalized_channels:
            self._active_subscription = ()
            self._subscription_acknowledged.set()
            return

        subscribe = getattr(self.client, "subscribe", None)
        if not callable(subscribe):
            raise TypeError("official SDK streaming client has no subscribe method")

        self._subscription_acknowledged.clear()
        performance_diagnostics.record_startup_stage("subscription_requested")
        performance_diagnostics.record_stream_observability(
            "SUBSCRIBE_REQUESTED", generation=self._generation,
            desired_count=len(normalized_channels), desired_fingerprint=desired_fp,
            added_count=len(set(normalized_channels) - set(prior_channels)),
            removed_count=len(set(prior_channels) - set(normalized_channels)),
            operation=subscription_operation,
        )
        performance_diagnostics.increment_startup_counter(
            "subscription_requested_symbols", len(normalized_channels)
        )
        performance_diagnostics.increment_startup_counter(
            "subscription_batch_count"
        )
        mapped = normalized_channels if self._subscription_mapper is None else self._subscription_mapper(normalized_channels)
        subscribe_started = monotonic()
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
                performance_diagnostics.record_stream_observability(
                    "SUBSCRIBE_FAILED", generation=self._generation,
                    desired_count=len(normalized_channels), desired_fingerprint=desired_fp,
                    operation=subscription_operation, exception_class=type(exc).__name__,
                    duration_ms=(monotonic() - subscribe_started) * 1000.0,
                )
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
            self._active_subscription_generation = self._generation
            performance_diagnostics.record_startup_stage("subscription_completed")
            performance_diagnostics.increment_startup_counter(
                "subscription_completed_symbols", len(normalized_channels)
            )
            self._notify("rest_subscription_active")
            performance_diagnostics.record_stream_observability(
                "SUBSCRIBE_COMPLETED", generation=self._generation,
                active_count=len(normalized_channels), active_fingerprint=desired_fp,
                operation=subscription_operation,
                duration_ms=(monotonic() - subscribe_started) * 1000.0,
            )
            self._emit_diagnostic(
                "REGISTRATION_REQUEST_SUCCEEDED",
                retry_count=retry_count,
            )
            return

    @property
    def heartbeat_ok(self) -> bool:
        return self._connected.is_set()

    @property
    def last_raw_callback_monotonic(self) -> float | None:
        return self._last_raw_callback_monotonic

    @property
    def last_raw_callback_at(self) -> datetime | None:
        return self._last_raw_callback_at

    @property
    def subscription_acknowledged(self) -> bool:
        return self._subscription_acknowledged.is_set()

    @property
    def registration_ready(self) -> bool:
        return self._registration_ready.is_set()

    def receive(self) -> object | None:
        while True:
            try:
                message = self._messages.get(timeout=self._receive_timeout_seconds)
            except Empty:
                return None
            if (
                isinstance(message, _ReceivedStreamPayload)
                and message.generation != self._generation
            ):
                performance_diagnostics.record_stream_observability(
                    "CALLBACK_RETIRED_GENERATION", generation=message.generation,
                    current_generation=self._generation,
                    reason_category="RETIRED_GENERATION",
                )
                performance_diagnostics.record_stream_stale_generation_rejection()
                with self._message_metrics_lock:
                    self._messages_dequeued += 1
                    self._messages_discarded_retired_generation += 1
                    self._message_queue_depth = max(
                        0, self._message_queue_depth - 1
                    )
                continue
            with self._message_metrics_lock:
                self._messages_dequeued += 1
                self._message_queue_depth -= 1
                if self._generation_first_dequeued_at is None:
                    self._generation_first_dequeued_at = self._clock()
                    self._startup_buffered_count = self._message_queue_depth + 1
                if isinstance(message, _ReceivedStreamPayload):
                    age_ms = max(
                        0.0,
                        (self._clock() - message.received_timestamp).total_seconds()
                        * 1000.0,
                    )
                    self._oldest_buffered_age_ms = age_ms
                    self._oldest_buffered_age_high_water_ms = max(
                        self._oldest_buffered_age_high_water_ms, age_ms,
                    )
                    self._dequeue_residence_ms.append(age_ms)
                    performance_diagnostics.record_component_duration(
                        "callback_queue_residence", age_ms,
                    )
            performance_diagnostics.increment_startup_counter("callbacks_dequeued")
            performance_diagnostics.record_startup_stage("first_callback_dequeued")
            if not self._consumption_started:
                self._consumption_started = True
                self._notify("active_event_consumption")
            return message

    def receive_nowait(self) -> object | None:
        while True:
            try:
                message = self._messages.get_nowait()
            except Empty:
                return None
            if (
                isinstance(message, _ReceivedStreamPayload)
                and message.generation != self._generation
            ):
                performance_diagnostics.record_stream_stale_generation_rejection()
                with self._message_metrics_lock:
                    self._messages_dequeued += 1
                    self._messages_discarded_retired_generation += 1
                    self._message_queue_depth = max(
                        0, self._message_queue_depth - 1
                    )
                continue
            with self._message_metrics_lock:
                self._messages_dequeued += 1
                self._message_queue_depth -= 1
                if self._generation_first_dequeued_at is None:
                    self._generation_first_dequeued_at = self._clock()
                    self._startup_buffered_count = self._message_queue_depth + 1
                if isinstance(message, _ReceivedStreamPayload):
                    age_ms = max(
                        0.0,
                        (self._clock() - message.received_timestamp).total_seconds()
                        * 1000.0,
                    )
                    self._oldest_buffered_age_ms = age_ms
                    self._oldest_buffered_age_high_water_ms = max(
                        self._oldest_buffered_age_high_water_ms, age_ms,
                    )
                    self._dequeue_residence_ms.append(age_ms)
                    performance_diagnostics.record_component_duration(
                        "callback_queue_residence", age_ms,
                    )
            performance_diagnostics.increment_startup_counter("callbacks_dequeued")
            performance_diagnostics.record_startup_stage("first_callback_dequeued")
            if not self._consumption_started:
                self._consumption_started = True
                self._notify("active_event_consumption")
            return message

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
        self.health = ConnectionHealth(); self.channels = ()
        self._events = []
        self._event_identities = set()
        self._payload_count = 0
        self._successful_receive_count = 0
        self._skipped_payload_count = 0
        self._skipped_payload_counts: dict[str, int] = {}
        self._stale_receive_count = 0
        self._last_event_by_ordering_key = {}
        self._stale_counts_by_event_type: dict[str, int] = {}
        self._stale_counts_by_symbol: dict[str, int] = {}
        self._cross_timeline_regression_count = 0
        self._last_normalized_event_monotonic: float | None = None
        self._last_normalized_event_at: datetime | None = None
        self._ordering_samples: list[dict[str, object]] = []
        self._decode_failure_counts: dict[str, int] = {}
        self._decode_failure_samples: list[dict[str, object]] = []
        self._decode_failure_signatures: set[tuple[str, str, str, str]] = set()
        self.lifecycle_sink = lifecycle_sink
        self.consecutive_decode_failure_threshold = consecutive_decode_failure_threshold
        self.consecutive_decode_failures = 0
        self._receive_loop_observed = False
        self._recovery_attempt_sequence = 0
        self._recovery_lock = Lock()
        self._recovery_owner: str | None = None
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

    @property
    def recovery_active(self) -> bool:
        with self._recovery_lock:
            return self._recovery_owner is not None

    def begin_recovery(self, owner: str) -> bool:
        """Claim the single transport replacement lifecycle."""
        owner = str(owner).strip().upper() or "UNKNOWN"
        with self._recovery_lock:
            if self._recovery_owner is not None:
                performance_diagnostics.record_stream_observability(
                    "RECOVERY_COALESCED",
                    controller=owner,
                    active_controller=self._recovery_owner,
                    reason_category="RECOVERY_ALREADY_ACTIVE",
                )
                return False
            self._recovery_owner = owner
            return True

    def end_recovery(self, owner: str) -> None:
        owner = str(owner).strip().upper() or "UNKNOWN"
        with self._recovery_lock:
            if self._recovery_owner == owner:
                self._recovery_owner = None

    @property
    def log(self) -> MarketEventLog:
        """Return a full-fidelity immutable diagnostic view on demand."""

        return MarketEventLog(tuple(self._events))

    @property
    def ordering_diagnostics(self) -> dict[str, object]:
        """Return bounded evidence about timestamp-order decisions."""

        return {
            "stale_total": self._stale_receive_count,
            "stale_by_event_type": tuple(sorted(self._stale_counts_by_event_type.items())),
            "stale_by_symbol": tuple(sorted(self._stale_counts_by_symbol.items())),
            "cross_timeline_regressions_accepted": self._cross_timeline_regression_count,
            "samples": tuple(dict(sample) for sample in self._ordering_samples),
        }

    @property
    def decoder_diagnostics(self) -> dict[str, object]:
        """Return bounded failure counts and sanitized first-seen evidence."""

        return {
            "received": self._payload_count,
            "successfully_decoded": self._successful_receive_count,
            "decode_failures_by_event_class": tuple(
                sorted(self._decode_failure_counts.items())
            ),
            "skipped_payloads_by_event_class": tuple(
                sorted(self._skipped_payload_counts.items())
            ),
            "samples": tuple(dict(sample) for sample in self._decode_failure_samples),
        }

    def memory_metrics(self) -> dict[str, object]:
        """Expose bounded backend queue metrics to memory observability."""
        provider = getattr(self.backend, "memory_metrics", None)
        return {} if not callable(provider) else dict(provider())

    def _notify(
        self,
        event: str,
        attempt: int,
        error: Exception | None = None,
    ) -> None:
        if self.lifecycle_sink is not None:
            self.lifecycle_sink(event, attempt, error)
        backend_metrics = getattr(self.backend, "generation_metrics", {})
        generation = backend_metrics.get("generation")
        session_hash = getattr(self.backend, "session_identity_hash", None)
        performance_diagnostics.record_stream_lifecycle(
            event,
            error=error,
            attempt=attempt,
            maximum_attempts=getattr(self.policy, "maximum_attempts", None),
            generation=generation,
            session_id_hash=session_hash if isinstance(session_hash, str) else None,
        )

    def _remember_ordering_sample(
        self,
        *,
        status: str,
        reason: str,
        event: object,
        previous: object,
    ) -> None:
        if len(self._ordering_samples) >= 12:
            return
        self._ordering_samples.append(
            {
                "status": status,
                "reason": reason,
                "symbol": getattr(event, "symbol", None) or "--",
                "event_type": _timestamp_channel(event),
                "sequence": getattr(event, "sequence"),
                "timestamp": getattr(event, "timestamp").isoformat(),
                "previous_symbol": getattr(previous, "symbol", None) or "--",
                "previous_event_type": _timestamp_channel(previous),
                "previous_sequence": getattr(previous, "sequence"),
                "previous_timestamp": getattr(previous, "timestamp").isoformat(),
            }
        )

    def connect(self):
        self._receive_loop_observed = False
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

    @property
    def last_raw_callback_monotonic(self) -> float | None:
        return getattr(self.backend, "last_raw_callback_monotonic", None)

    @property
    def last_raw_callback_at(self) -> datetime | None:
        return getattr(self.backend, "last_raw_callback_at", None)

    @property
    def generation_metrics(self) -> dict[str, object]:
        return dict(getattr(self.backend, "generation_metrics", {}))

    @property
    def last_normalized_event_monotonic(self) -> float | None:
        return getattr(self, "_last_normalized_event_monotonic", None)

    @property
    def last_normalized_event_at(self) -> datetime | None:
        return getattr(self, "_last_normalized_event_at", None)

    def receive(self):
        if not self._receive_loop_observed:
            self._receive_loop_observed = True
            performance_diagnostics.record_stream_observability(
                "RECEIVE_LOOP_STARTED",
                generation=getattr(self.backend, "generation_metrics", {}).get("generation"),
            )
        return self._receive_from(self.backend.receive)

    def receive_nowait(self):
        receive_nowait = getattr(self.backend, "receive_nowait", None)
        if not callable(receive_nowait):
            return None
        return self._receive_from(receive_nowait)

    def _receive_from(self, receiver):
        network_attempt = 0
        while network_attempt <= self.policy.maximum_attempts:
            try:
                message = receiver()
                if message is None: return None
                performance_diagnostics.record_startup_stage(
                    "first_payload_decode_attempt"
                )
                performance_diagnostics.increment_startup_counter("decode_attempts")
                queue_depth = getattr(self.backend, "queue_depth", None)
                if callable(queue_depth):
                    performance_diagnostics.set_callback_queue_depth(queue_depth())
                if isinstance(message, _ReceivedStreamPayload):
                    received_timestamp = message.received_timestamp
                    dequeued_timestamp = datetime.now(UTC)
                    message = (message.topic, message.payload)
                else:
                    received_timestamp = None
                    dequeued_timestamp = None
                diagnostic = payload_metadata(message)
                classification = str(diagnostic["message_classification"])
                self._payload_count += 1
                decoder = {
                    "QUOTE": "QuoteDecoder/QuoteResult",
                    "TRADE": "TickDecoder/TickResult",
                    "SNAPSHOT": "SnapshotDecoder/SnapshotResult",
                }.get(classification, "none")
                self.decoder_health = "STREAM_DECODING"
                if self._payload_count == 1 or self._payload_count % 1000 == 0:
                    self.logger.log(
                        "stream_payload", "classified",
                        message_classification=classification,
                        decoder_selected=decoder,
                        market_events_received=self._payload_count,
                    )
                event = self.parser(message)
                if event is None:
                    performance_diagnostics.increment_startup_counter("decode_ignored")
                    recognized = classification != "UNKNOWN"
                    recovered = recognized and self.consecutive_decode_failures > 0
                    if recognized:
                        self.consecutive_decode_failures = 0
                    self.decoder_health = (
                        "STREAM_CONNECTED" if recognized
                        else "STREAM_PAYLOAD_UNSUPPORTED"
                    )
                    if recovered:
                        self._notify("decode_recovered", 0)
                    self._skipped_payload_count += 1
                    self._skipped_payload_counts[classification] = (
                        self._skipped_payload_counts.get(classification, 0) + 1
                    )
                    if self._skipped_payload_count == 1 or self._skipped_payload_count % 1000 == 0:
                        self.logger.log(
                            "stream_payload", "skipped",
                            message_classification=classification,
                            skipped_payload_count=self._skipped_payload_count,
                            decoder_selected=decoder,
                        )
                    return None
                performance_diagnostics.increment_startup_counter("decode_successes")
                performance_diagnostics.record_startup_stage(
                    "first_payload_decode_success"
                )
                if received_timestamp is not None:
                    event = replace(
                        event,
                        received_timestamp=received_timestamp,
                        dequeued_timestamp=dequeued_timestamp,
                    )
                performance_diagnostics.increment("market_events_received")
                try:
                    validate_event(event)
                except ValueError as exc:
                    self.logger.log(
                        "stream_receive",
                        "skipped",
                        event_type=event.event_type.value,
                        symbol=event.symbol or "--",
                        reason="invalid_market_event",
                        error_type=type(exc).__name__,
                    )
                    return None

                try:
                    identity = (event.source, event.sequence)
                    if identity in self._event_identities:
                        return None
                    previous = self._events[-1] if self._events else None
                    if previous is not None:
                        if event.sequence <= previous.sequence:
                            raise ValueError("sequence must increase monotonically")
                    ordering_key = _timestamp_ordering_key(event)
                    previous_on_timeline = self._last_event_by_ordering_key.get(
                        ordering_key
                    )
                    if (
                        previous_on_timeline is not None
                        and event.timestamp < previous_on_timeline.timestamp
                    ):
                        raise ValueError("timestamp must not move backwards")
                except ValueError as exc:
                    previous = self._events[-1] if self._events else None
                    ordering_key = _timestamp_ordering_key(event)
                    previous_on_timeline = self._last_event_by_ordering_key.get(
                        ordering_key
                    )
                    if (
                        previous_on_timeline is not None
                        and previous is not None
                        and event.sequence > previous.sequence
                        and event.timestamp < previous_on_timeline.timestamp
                    ):
                        # Timestamp freshness is meaningful only within one
                        # source/symbol/channel timeline. MQTT receive order is
                        # still enforced globally by the parser sequence.
                        performance_diagnostics.increment("stale_events_skipped")
                        self._stale_receive_count += 1
                        event_type = _timestamp_channel(event)
                        symbol = event.symbol or "--"
                        self._stale_counts_by_event_type[event_type] = (
                            self._stale_counts_by_event_type.get(event_type, 0) + 1
                        )
                        self._stale_counts_by_symbol[symbol] = (
                            self._stale_counts_by_symbol.get(symbol, 0) + 1
                        )
                        self._remember_ordering_sample(
                            status="skipped",
                            reason="same_timeline_timestamp_regression",
                            event=event,
                            previous=previous_on_timeline,
                        )
                        # Preserve the first diagnostic and periodic aggregate
                        # samples without turning logging into a raw tick tape.
                        if self._stale_receive_count == 1 or self._stale_receive_count % 1000 == 0:
                            self.logger.log(
                                "stream_receive",
                                "skipped",
                                event_type=event_type,
                                symbol=symbol,
                                stale_events_skipped=self._stale_receive_count,
                                stale_by_event_type=dict(
                                    sorted(self._stale_counts_by_event_type.items())
                                ),
                                reason="same_timeline_timestamp_regression",
                            )
                        return None
                    raise _StreamSequenceError(
                        "invalid Webull stream sequence"
                    ) from exc
                previous = self._events[-1] if self._events else None
                if previous is not None and event.timestamp < previous.timestamp:
                    self._cross_timeline_regression_count += 1
                    self._remember_ordering_sample(
                        status="accepted",
                        reason="independent_timeline_timestamp_regression",
                        event=event,
                        previous=previous,
                    )
                    if (
                        self._cross_timeline_regression_count == 1
                        or self._cross_timeline_regression_count % 1000 == 0
                    ):
                        self.logger.log(
                            "stream_receive",
                            "accepted",
                            event_type=_timestamp_channel(event),
                            symbol=event.symbol or "--",
                            cross_timeline_regressions_accepted=(
                                self._cross_timeline_regression_count
                            ),
                            reason="independent_timeline_timestamp_regression",
                        )
                self._events.append(event)
                self._last_normalized_event_monotonic = monotonic()
                self._last_normalized_event_at = event.timestamp
                self._event_identities.add((event.source, event.sequence))
                self._last_event_by_ordering_key[_timestamp_ordering_key(event)] = event
                recovered = self.consecutive_decode_failures > 0
                self.consecutive_decode_failures = 0
                self.decoder_health = "STREAM_CONNECTED"
                if recovered:
                    self._notify("decode_recovered", 0)
                if event.event_type is MarketEventType.HEARTBEAT and isinstance(event.payload, HeartbeatPayload):
                    self.health = update_health(self.health, last_successful_heartbeat=event.timestamp)
                self._successful_receive_count += 1
                performance_diagnostics.record_stream_normalized_event()
                performance_diagnostics.increment_startup_counter(
                    "normalized_market_events_emitted"
                )
                performance_diagnostics.record_startup_stage(
                    "first_normalized_market_event"
                )
                if self._successful_receive_count == 1 or self._successful_receive_count % 1000 == 0:
                    self.logger.log(
                        "stream_receive", "succeeded",
                        event_type=event.event_type.value,
                        market_events_received=self._successful_receive_count,
                        decoder_health=self.decoder_health,
                    )
                return event
            except _StreamSequenceError:
                raise
            except SerializationError as exc:
                performance_diagnostics.increment_startup_counter("decode_failures")
                self.consecutive_decode_failures += 1
                failure_metadata = decoder_failure_metadata(message, exc)
                failure_class = str(failure_metadata["message_classification"])
                failure_signature = (
                    failure_class,
                    str(failure_metadata["sdk_object_type"]),
                    str(failure_metadata["failure_field"]),
                    str(failure_metadata["error_stage"]),
                )
                failure_count = self._decode_failure_counts.get(failure_class, 0) + 1
                self._decode_failure_counts[failure_class] = failure_count
                if (
                    len(self._decode_failure_samples) < 12
                    and failure_signature not in self._decode_failure_signatures
                ):
                    self._decode_failure_signatures.add(failure_signature)
                    self._decode_failure_samples.append(dict(failure_metadata))
                threshold_reached = (
                    self.consecutive_decode_failures
                    >= self.consecutive_decode_failure_threshold
                )
                self.decoder_health = (
                    "STREAM_FAILED" if threshold_reached
                    else "STREAM_PARTIALLY_DEGRADED"
                )
                if failure_count == 1 or failure_count % 100 == 0 or threshold_reached:
                    self.logger.log(
                        "stream_receive", "decode_failed", **failure_metadata,
                        error_type=type(exc).__name__,
                        decode_failure_count=failure_count,
                        consecutive_decode_failures=self.consecutive_decode_failures,
                        decoder_health=self.decoder_health,
                    )
                if threshold_reached:
                    self._notify(
                        "decode_threshold_exceeded",
                        self.consecutive_decode_failures,
                        exc,
                    )
                elif self.consecutive_decode_failures == 1:
                    self._notify("parse_failed", 1, exc)
                if threshold_reached:
                    raise
                continue
            except Exception as exc:
                if self.recovery_active:
                    performance_diagnostics.record_stream_observability(
                        "RECOVERY_COALESCED",
                        controller="TRANSPORT",
                        reason_category="EXTERNAL_RECOVERY_ACTIVE",
                    )
                    return None
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
                self._recovery_attempt_sequence += 1
                recovery_id = self._recovery_attempt_sequence
                performance_diagnostics.record_stream_observability(
                    "RECOVERY_STARTED", controller="TRANSPORT",
                    recovery_attempt_id=recovery_id,
                    generation=getattr(self.backend, "generation_metrics", {}).get("generation"),
                    trigger_category=type(exc).__name__,
                )
                if not self.begin_recovery("TRANSPORT"):
                    return None
                try:
                    self.sleeper(self.policy.backoff_seconds)
                    self.backend.connect()
                    self.backend.subscribe(self.channels)
                except Exception as reconnect_error:
                    performance_diagnostics.record_stream_observability(
                        "RECOVERY_FAILED", controller="TRANSPORT",
                        recovery_attempt_id=recovery_id,
                        generation=getattr(self.backend, "generation_metrics", {}).get("generation"),
                        exception_class=type(reconnect_error).__name__,
                    )
                    self._notify(
                        "reconnect_failed",
                        self.health.reconnect_count + 1,
                        reconnect_error,
                    )
                    self._notify(
                        "terminal_failure",
                        self.health.reconnect_count,
                        reconnect_error,
                    )
                    raise
                finally:
                    self.end_recovery("TRANSPORT")
                network_attempt += 1
                self.health = update_health(self.health, websocket_connected=True, reconnect_count=self.health.reconnect_count + 1)
                self._notify(
                    "reconnected",
                    self.health.reconnect_count,
                )
                performance_diagnostics.record_stream_observability(
                    "RECOVERY_SUCCEEDED", controller="TRANSPORT",
                    recovery_attempt_id=recovery_id,
                    generation=getattr(self.backend, "generation_metrics", {}).get("generation"),
                )
                self.logger.log("stream_reconnect", "succeeded", reconnect_count=self.health.reconnect_count)


def _timestamp_channel(event: object) -> str:
    event_type = getattr(getattr(event, "event_type", None), "value", "UNKNOWN")
    trade_id = str(getattr(getattr(event, "payload", None), "trade_id", ""))
    if event_type == MarketEventType.TRADE.value and trade_id.startswith("snapshot"):
        return "SNAPSHOT"
    return str(event_type)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, int((len(values) - 1) * fraction))
    return float(values[index])


def _timestamp_ordering_key(event: object) -> tuple[str, str, str]:
    return (
        str(getattr(event, "source", "")),
        str(getattr(event, "symbol", None) or ""),
        _timestamp_channel(event),
    )

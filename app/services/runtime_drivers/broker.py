"""Broker lifecycle driver for a desktop runtime session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from queue import Empty, Queue
from threading import Event, RLock, Thread

from app.broker_plugins import BrokerRuntime
from app.configuration import OperationalConfiguration
from app.live_execution.account_polling import (
    BrokerAccountSnapshot,
    poll_broker_account,
)
from app.market_data.models import MarketEvent
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeEventSink,
    RuntimeHealthUpdate,
    RuntimeWatchlistUpdate,
)
from app.services.market_event_translation import translate_market_event


Clock = Callable[[], datetime]
AccountPoller = Callable[..., BrokerAccountSnapshot]
AccountSnapshotSink = Callable[[BrokerAccountSnapshot], None]
MarketEventTranslator = Callable[..., PaperRuntimeEvent | None]


def utc_now() -> datetime:
    return datetime.now(UTC)


class DesktopBrokerRuntimeDriver:
    """Own one configured broker connection for a desktop runtime session."""

    def __init__(
        self,
        *,
        configuration: OperationalConfiguration,
        broker_runtime: BrokerRuntime,
        event_sink: RuntimeEventSink,
        account_snapshot_sink: AccountSnapshotSink,
        account_poller: AccountPoller = poll_broker_account,
        market_event_translator: MarketEventTranslator = (
            translate_market_event
        ),
        clock: Clock = utc_now,
        source: str = "desktop-broker-runtime",
    ) -> None:
        if not isinstance(configuration, OperationalConfiguration):
            raise TypeError(
                "configuration must be OperationalConfiguration"
            )
        if not isinstance(broker_runtime, BrokerRuntime):
            raise TypeError("broker_runtime must be BrokerRuntime")
        if broker_runtime.execution is None:
            raise ValueError(
                "configured broker runtime has no execution service"
            )
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")
        if not callable(account_snapshot_sink):
            raise TypeError("account_snapshot_sink must be callable")
        if not callable(account_poller):
            raise TypeError("account_poller must be callable")
        if not callable(market_event_translator):
            raise TypeError("market_event_translator must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be non-empty text")

        self._configuration = configuration
        self._broker_runtime = broker_runtime
        self._broker = broker_runtime.execution
        self._event_sink = event_sink
        self._account_snapshot_sink = account_snapshot_sink
        self._account_poller = account_poller
        self._market_event_translator = market_event_translator
        self._market_data = broker_runtime.market_data
        self._clock = clock
        self._source = source.strip()
        self._sequence = 0
        self._event_lock = RLock()
        self._connected = False
        self._market_data_connected = False
        self._market_data_thread: Thread | None = None
        self._market_data_stop = Event()
        self._market_data_failures: Queue[Exception] = Queue()
        self._terminal_stream_failure_published = False
        self._cycles_completed = 0

    @property
    def environment(self) -> str:
        return self._configuration.environment.value

    @property
    def active_model(self) -> str:
        return f"{self._broker_runtime.provider} broker"

    @property
    def cycles_completed(self) -> int:
        return self._cycles_completed

    def run(
        self,
        *,
        stop_event: Event,
        cycle_sink: Callable[[int], None],
    ) -> None:
        if not isinstance(stop_event, Event):
            raise TypeError("stop_event must be a threading Event")
        if not callable(cycle_sink):
            raise TypeError("cycle_sink must be callable")

        self._publish(
            "BROKER_CONNECTING",
            "Connecting to the configured broker.",
            runtime_status="STARTING",
            broker_status="CONNECTING",
        )

        try:
            self._broker.connect()
            self._connected = True
        except Exception as exc:
            self._publish(
                "BROKER_AUTHENTICATION_FAILED",
                "Configured broker authentication failed.",
                runtime_status="FAILED",
                broker_status="DISCONNECTED",
                last_error=f"{type(exc).__name__}: {exc}",
            )
            raise

        self._publish(
            "BROKER_AUTHENTICATED",
            "Configured broker connected and authenticated.",
            runtime_status="RUNNING",
            broker_status="CONNECTED",
        )

        try:
            self._start_market_data(stop_event)
            self._poll_accounts(
                stop_event=stop_event,
                cycle_sink=cycle_sink,
            )
            self._raise_market_data_failure()
        finally:
            try:
                self._stop_market_data()
            finally:
                self._disconnect()

    def _start_market_data(self, stop_event: Event) -> None:
        if self._market_data is None:
            if self._configuration.market_data_streaming_enabled:
                raise RuntimeError(
                    "configured broker runtime has no market-data service"
                )
            return

        self._publish_health(
            "MARKET_DATA_CONNECTING",
            "Connecting to Webull market data.",
            RuntimeHealthUpdate(market_data_status="CONNECTING"),
        )
        lifecycle_setter = getattr(
            self._market_data,
            "set_lifecycle_sink",
            None,
        )
        if callable(lifecycle_setter):
            lifecycle_setter(self._on_market_data_lifecycle)

        try:
            self._market_data.connect()
            self._market_data_connected = True
            self._publish_health(
                "MARKET_DATA_CONNECTED",
                "Connected to Webull market data.",
                RuntimeHealthUpdate(market_data_status="CONNECTED"),
            )
            self._market_data.subscribe(
                self._configuration.market_data_symbols
            )
            for symbol in self._configuration.market_data_symbols:
                self._emit(
                    PaperRuntimeEvent(
                        sequence=self._next_sequence(),
                        timestamp=self._timestamp(),
                        event_type="SYMBOL_SUBSCRIBED",
                        message=f"Subscribed to live market data for {symbol}.",
                        cycle=self._cycles_completed,
                        symbol=symbol,
                        source=self._source,
                        watchlist=RuntimeWatchlistUpdate(
                            symbol=symbol,
                            subscribed=True,
                        ),
                    )
                )
            self._publish_health(
                "MARKET_DATA_SUBSCRIBED",
                "Subscribed to configured Webull market-data symbols.",
                RuntimeHealthUpdate(market_data_status="SUBSCRIBED"),
            )
        except Exception as exc:
            self._publish_terminal_market_data_failure(exc)
            raise

        self._market_data_stop.clear()
        self._market_data_thread = Thread(
            target=self._receive_market_data,
            args=(stop_event,),
            name="desktop-market-data",
        )
        self._market_data_thread.start()

    def _receive_market_data(self, stop_event: Event) -> None:
        try:
            while (
                not stop_event.is_set()
                and not self._market_data_stop.is_set()
            ):
                event = self._market_data.read_event()
                if event is None:
                    self._publish_health(
                        "MARKET_DATA_HEARTBEAT",
                        "Webull market-data receive loop is healthy.",
                        RuntimeHealthUpdate(
                            market_data_status="CONNECTED",
                        ),
                    )
                    continue
                if not isinstance(event, MarketEvent):
                    raise TypeError(
                        "market-data transport returned a non-MarketEvent"
                    )
                translated = self._market_event_translator(
                    event,
                    sequence=self._next_sequence(),
                    source=self._source,
                    cycle=self._cycles_completed,
                )
                if translated is not None:
                    self._emit(translated)
        except Exception as exc:
            self._publish_terminal_market_data_failure(exc)
            self._market_data_failures.put(exc)
            stop_event.set()

    def _on_market_data_lifecycle(
        self,
        lifecycle: str,
        attempt: int,
        error: Exception | None,
    ) -> None:
        detail = (
            None
            if error is None
            else f"{type(error).__name__}: {error}"
        )
        if lifecycle == "reconnecting":
            self._publish_health(
                "MARKET_DATA_RECONNECTING",
                "Reconnecting to Webull market data.",
                RuntimeHealthUpdate(
                    market_data_status="RECONNECTING",
                    last_warning=detail,
                    reconnect_attempts=attempt,
                ),
            )
        elif lifecycle == "reconnected":
            self._publish_health(
                "MARKET_DATA_RECONNECTED",
                "Reconnected to Webull market data.",
                RuntimeHealthUpdate(
                    market_data_status="CONNECTED",
                    reconnect_attempts=attempt,
                ),
            )
        elif lifecycle == "parse_failed":
            self._publish_health(
                "MARKET_DATA_PARSE_FAILED",
                "Webull market-data payload could not be decoded.",
                RuntimeHealthUpdate(
                    market_data_status="ERROR",
                    last_error=detail,
                    reconnect_attempts=attempt,
                ),
            )
        elif lifecycle == "terminal_failure" and error is not None:
            self._publish_terminal_market_data_failure(error)

    def _publish_terminal_market_data_failure(
        self,
        error: Exception,
    ) -> None:
        with self._event_lock:
            if self._terminal_stream_failure_published:
                return
            self._terminal_stream_failure_published = True
        self._publish_health(
            "MARKET_DATA_TERMINAL_FAILURE",
            "Webull market-data streaming failed.",
            RuntimeHealthUpdate(
                runtime_status="FAILED",
                market_data_status="FAILED",
                last_error=f"{type(error).__name__}: {error}",
            ),
        )

    def _raise_market_data_failure(self) -> None:
        try:
            failure = self._market_data_failures.get_nowait()
        except Empty:
            return
        raise failure

    def _stop_market_data(self) -> None:
        if self._market_data is None:
            return
        self._market_data_stop.set()
        thread = self._market_data_thread
        if thread is not None:
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise RuntimeError(
                    "market-data receive worker did not stop cooperatively"
                )
            self._market_data_thread = None

        if not self._market_data_connected:
            return
        try:
            self._market_data.disconnect()
        except Exception as exc:
            self._publish_terminal_market_data_failure(exc)
            raise
        finally:
            self._market_data_connected = False

        self._publish_health(
            "MARKET_DATA_DISCONNECTED",
            "Webull market data disconnected.",
            RuntimeHealthUpdate(market_data_status="DISCONNECTED"),
        )

    def _poll_accounts(
        self,
        *,
        stop_event: Event,
        cycle_sink: Callable[[int], None],
    ) -> None:
        interval_seconds = (
            self._configuration.reconciliation_interval_seconds
        )
        while not stop_event.is_set():
            snapshot = self._account_poller(
                self._broker,
                clock=self._clock,
            )
            self._account_snapshot_sink(snapshot)
            self._cycles_completed += 1
            cycle_sink(self._cycles_completed)
            if stop_event.wait(interval_seconds):
                break

    def _disconnect(self) -> None:
        if not self._connected:
            return

        try:
            self._broker.disconnect()
        except Exception as exc:
            self._publish(
                "BROKER_DISCONNECT_FAILED",
                "Configured broker disconnect failed.",
                runtime_status="FAILED",
                broker_status="ERROR",
                last_error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            self._connected = False

        self._publish(
            "BROKER_DISCONNECTED",
            "Configured broker disconnected.",
            runtime_status="STOPPED",
            broker_status="DISCONNECTED",
        )

    def _publish(
        self,
        event_type: str,
        message: str,
        *,
        runtime_status: str,
        broker_status: str,
        last_error: str | None = None,
    ) -> None:
        timestamp = self._timestamp()
        self._emit(
            PaperRuntimeEvent(
                sequence=self._next_sequence(),
                timestamp=timestamp,
                event_type=event_type,
                message=message,
                cycle=0,
                source=self._source,
                health=RuntimeHealthUpdate(
                    runtime_status=runtime_status,
                    broker_status=broker_status,
                    last_error=last_error,
                ),
            )
        )

    def _publish_health(
        self,
        event_type: str,
        message: str,
        health: RuntimeHealthUpdate,
    ) -> None:
        self._emit(
            PaperRuntimeEvent(
                sequence=self._next_sequence(),
                timestamp=self._timestamp(),
                event_type=event_type,
                message=message,
                cycle=self._cycles_completed,
                source=self._source,
                health=health,
            )
        )

    def _timestamp(self) -> datetime:
        timestamp = self._clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("broker runtime clock must be timezone-aware")
        return timestamp

    def _next_sequence(self) -> int:
        with self._event_lock:
            self._sequence += 1
            return self._sequence

    def _emit(self, event: PaperRuntimeEvent) -> None:
        with self._event_lock:
            self._event_sink(event)


__all__ = ["DesktopBrokerRuntimeDriver", "utc_now"]

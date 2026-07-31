"""Broker lifecycle driver for a desktop runtime session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from queue import Empty, Queue
from threading import Event, RLock, Thread

from app.broker_plugins import BrokerRuntime
from app.configuration import OperationalConfiguration
from app.live_execution.account_polling import (
    BrokerAccountSnapshot,
    poll_broker_account,
)
from app.market_data.models import MarketEvent
from app.momentum_scanner import AssetClass
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeEventSink,
    RuntimeHealthUpdate,
    RuntimeWatchlistUpdate,
)
from app.operations.scanner_snapshot_publisher import ScannerSnapshotPublisher
from app.services.market_event_translation import translate_market_event
from app.webull.client_factories import market_data_configuration


Clock = Callable[[], datetime]
AccountPoller = Callable[..., BrokerAccountSnapshot]
AccountSnapshotSink = Callable[[BrokerAccountSnapshot], None]
MarketEventTranslator = Callable[..., PaperRuntimeEvent | None]
MarketEventObserver = Callable[[MarketEvent], object]


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
        market_event_observer: MarketEventObserver | None = None,
        scanner_coordinator: object | None = None,
        market_data_probe: object | None = None,
        startup_validator: object | None = None,
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
        if (
            market_event_observer is not None
            and not callable(market_event_observer)
        ):
            raise TypeError(
                "market_event_observer must be callable or None"
            )
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
        self._market_event_observer = market_event_observer
        self._scanner = scanner_coordinator
        self._market_data_probe = market_data_probe
        self._startup_validator = startup_validator
        self._startup_validation = None
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
        self._scanner_publisher = ScannerSnapshotPublisher(
            self._event_sink,
            self._next_sequence,
            source=self._source,
            stale_after=timedelta(
                seconds=configuration.maximum_market_data_age_seconds
            ),
        )

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

        if self._startup_validator is not None:
            self._startup_validation = self._startup_validator.run()

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
                last_error=_safe_runtime_error(exc),
            )
            raise

        self._publish(
            "BROKER_AUTHENTICATED",
            "Configured broker connected and authenticated.",
            runtime_status="RUNNING",
            broker_status="CONNECTED",
            trading_ready=(
                self._startup_validation.trading.ready
                if self._startup_validation is not None
                else True
            ),
        )
        if self._startup_validation is not None:
            self._publish_startup_validation(self._startup_validation)

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
        if (
            self._startup_validation is not None
            and not self._startup_validation.scanner_ready
        ):
            if self._scanner is not None:
                self._scanner.disconnect()
            elif self._market_data is not None:
                self._market_data.disconnect()
            return
        if self._market_data is None:
            if self._configuration.market_data_streaming_enabled:
                cfg = market_data_configuration(self._configuration)
                reason = (
                    "Production market-data credentials are missing."
                    if not cfg.api_key.strip() or not cfg.api_secret.strip()
                    else "Configured broker runtime has no market-data service."
                )
                self._publish_health(
                    "MARKET_DATA_STARTUP_ERROR",
                    reason,
                    RuntimeHealthUpdate(
                        market_data_status="DISABLED",
                        market_data_environment=cfg.environment.value,
                        market_data_rest_status="DISABLED",
                        streaming_status="DISABLED",
                        entitlement_status="UNKNOWN",
                        scanner_status="DISABLED",
                        last_warning=reason,
                    ),
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
            if self._scanner is not None:
                if (
                    self._market_data_probe is not None
                    and self._startup_validation is None
                ):
                    result = self._market_data_probe.run()
                    self._publish_probe_result(result)
                    if not result.scanner_ready:
                        self._scanner.disconnect()
                        return
                scanner_active = self._start_scanner()
                if scanner_active:
                    self._market_data_stop.clear()
                    self._market_data_thread = Thread(
                        target=self._receive_market_data,
                        args=(stop_event,),
                        name="desktop-market-data",
                    )
                    self._market_data_thread.start()
                return
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

    def _publish_startup_validation(self, result: object) -> None:
        trading = result.trading
        market_data = result.market_data
        self._publish_health(
            "TRADING_STARTUP_VALIDATED",
            "Trading startup validation completed.",
            RuntimeHealthUpdate(
                trading_environment=trading.environment,
                trading_rest_status=str(trading.authentication),
                account_status=str(trading.account),
                buying_power_status=str(trading.buying_power),
                positions_status=str(trading.positions),
                orders_status=str(trading.paper_trading),
                balances_status=str(trading.buying_power),
                scanner_status=(
                    "VALIDATING" if trading.ready else "DISABLED"
                ),
                last_warning=None if trading.ready else result.reason,
            ),
        )
        self._scanner_log(
            "trading_capabilities",
            f"Trading({trading.environment}) "
            f"fingerprint={trading.fingerprint} "
            f"REST={trading.authentication} account={trading.account} "
            f"buying_power={trading.buying_power} "
            f"positions={trading.positions} orders={trading.paper_trading}",
        )
        self._publish_probe_result(market_data)
        if not result.scanner_ready:
            self._publish_health(
                "STARTUP_VALIDATION_FAILED",
                result.reason or "Startup validation failed.",
                RuntimeHealthUpdate(
                    scanner_status="DISABLED",
                    last_warning=result.reason or "Startup validation failed.",
                ),
            )

    def _start_scanner(self) -> bool:
        scanner = self._scanner
        assert scanner is not None
        self._scanner_log(
            "scanner_initialized",
            "Autonomous scanner components initialized.",
        )
        observer_setter = getattr(scanner, "set_event_observer", None)
        if callable(observer_setter):
            observer_setter(self._handle_market_event)
        try:
            active_symbols = scanner.start(
                asset_classes=(AssetClass.STOCK,),
                force_reference_refresh=True,
            )
        except Exception as exc:
            try:
                scanner.disconnect()
            finally:
                self._scanner_error(
                    "Scanner initialization failed.",
                    exc,
                )
            raise

        self._market_data_connected = True
        warmup_snapshot = scanner.snapshot()
        if not active_symbols:
            reason = (
                getattr(warmup_snapshot, "health_reason", None)
                or "No symbols survived scanner reference warmup."
            )
            self._scanner_publisher.publish(
                warmup_snapshot,
                cycle=self._cycles_completed,
                now=self._timestamp(),
            )
            self._scanner_log(
                "scanner_empty_fail_closed",
                reason,
                health=RuntimeHealthUpdate(
                    market_data_status="NO_SUPPORTED_SYMBOLS",
                    scanner_status="NO_SUPPORTED_SYMBOLS",
                    supported_symbols=0,
                    last_warning=reason,
                ),
            )
            return False
        if warmup_snapshot.reference_failures:
            details = "; ".join(
                f"{failure.symbol}: {failure.reason}"
                for failure in warmup_snapshot.reference_failures
            )
            self._scanner_log(
                "scanner_error",
                "Some scanner reference data could not be loaded.",
                health=RuntimeHealthUpdate(last_warning=details),
            )
        self._scanner_log(
            "universe_refreshed",
            "Eligible US-stock universe refreshed.",
        )
        self._scanner_log(
            "symbols_eligible",
            f"{len(active_symbols)} symbols are eligible.",
        )
        self._scanner_log(
            "market_data_connected",
            "Official Webull market-data transport connected.",
            health=RuntimeHealthUpdate(
                market_data_status="CONNECTED",
                streaming_status="CONNECTED",
            ),
        )
        self._scanner_log(
            "channels_subscribed",
            f"Subscribed quote and trade channels for "
            f"{len(active_symbols)} symbols.",
            health=RuntimeHealthUpdate(
                market_data_status="SUBSCRIBED",
                streaming_status="CONNECTED",
                scanner_status="ACTIVE",
                supported_symbols=len(active_symbols),
            ),
        )
        return True

    def _publish_probe_result(self, result: object) -> None:
        reason = getattr(result, "reason", None)
        ready = bool(getattr(result, "scanner_ready", False))
        entitlement = getattr(
            getattr(result, "entitlement", None), "state", "UNKNOWN"
        )
        endpoint = getattr(
            getattr(result, "endpoint", None), "state", "UNKNOWN"
        )
        streaming = getattr(
            getattr(result, "streaming", None), "state", "UNKNOWN"
        )
        subscription = getattr(
            getattr(result, "subscription", None), "state", "UNKNOWN"
        )
        heartbeat = getattr(
            getattr(result, "heartbeat", None), "state", "UNKNOWN"
        )
        reconnect = getattr(
            getattr(result, "reconnect", None), "state", "UNKNOWN"
        )
        bars = getattr(getattr(result, "bars", None), "state", "UNKNOWN")
        symbol_results = {
            item.symbol: str(item.result)
            for item in getattr(result, "symbol_results", ())
        }
        self._publish_health(
            "MARKET_DATA_PROBE_COMPLETED",
            "Market-data startup capability probe completed."
            if ready else str(reason),
            RuntimeHealthUpdate(
                market_data_status=(
                    "PROBED" if ready
                    else "NO_SUPPORTED_SYMBOLS" if str(bars) == "UNSUPPORTED"
                    else "STREAM_CONNECTED_SUBSCRIPTION_DENIED"
                    if str(streaming) == "AVAILABLE"
                    and str(subscription) == "UNAVAILABLE"
                    else "DISABLED"
                ),
                market_data_environment=getattr(result, "environment", "UNKNOWN"),
                market_data_rest_status=(
                    "CONNECTED" if str(endpoint) == "AVAILABLE" else str(endpoint)
                ),
                streaming_status=(
                    "CONNECTED" if str(streaming) == "AVAILABLE" else str(streaming)
                ),
                subscription_status=(
                    "ACCEPTED" if str(subscription) == "AVAILABLE"
                    else "DENIED"
                    if str(streaming) == "AVAILABLE"
                    and str(subscription) == "UNAVAILABLE"
                    else str(subscription)
                ),
                heartbeat_status=(
                    "OK" if str(heartbeat) == "AVAILABLE" else str(heartbeat)
                ),
                reconnect_status=(
                    "READY" if str(reconnect) == "AVAILABLE" else str(reconnect)
                ),
                entitlement_status=(
                    "GRANTED" if str(entitlement) == "AVAILABLE" else str(entitlement)
                ),
                scanner_status=(
                    "WARMING" if ready
                    else "NO_SUPPORTED_SYMBOLS" if str(bars) == "UNSUPPORTED"
                    else "DISABLED"
                ),
                probe_aapl_status=symbol_results.get("AAPL", "NOT_TESTED"),
                probe_spy_status=symbol_results.get("SPY", "NOT_TESTED"),
                probe_tsla_status=symbol_results.get("TSLA", "NOT_TESTED"),
                last_warning=None if ready else str(reason),
            ),
        )
        self._scanner_log(
            "market_data_capabilities",
            f"REST={endpoint} "
            f"streaming={streaming} "
            f"subscription={subscription} heartbeat={heartbeat} "
            f"reconnect={reconnect} "
            f"entitlement={entitlement} "
            f"fingerprint={getattr(result, 'credential_fingerprint', 'fp_missing')}",
        )

    def _receive_market_data(self, stop_event: Event) -> None:
        try:
            while (
                not stop_event.is_set()
                and not self._market_data_stop.is_set()
            ):
                if self._scanner is not None:
                    cycle = self._scanner.run_available()
                    snapshot = self._scanner.snapshot()
                    now = self._timestamp()
                    stale = self._scanner_publisher.publish(
                        snapshot,
                        cycle=self._cycles_completed,
                        now=now,
                    )
                    self._scanner_log(
                        "scanner_cycle",
                        "Autonomous scanner cycle completed.",
                    )
                    self._scanner_log(
                        "events_consumed",
                        f"Consumed {cycle.events_read} market events.",
                    )
                    self._scanner_log(
                        "scanner_snapshot_published",
                        f"Published {len(snapshot.ranked_candidates)} "
                        "ranked candidates.",
                    )
                    if stale:
                        self._scanner_error(
                            "Scanner quotes became stale: "
                            + ", ".join(stale),
                            RuntimeError("stale scanner quotes"),
                        )
                    continue
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
                self._handle_market_event(event)
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
            else type(error).__name__
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
                last_error=type(error).__name__,
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
            if self._scanner is not None:
                self._scanner.stop()
                self._scanner.disconnect()
            else:
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

    def _handle_market_event(self, event: MarketEvent) -> None:
        if not isinstance(event, MarketEvent):
            raise TypeError("market-data transport returned a non-MarketEvent")
        translated = self._market_event_translator(
            event,
            sequence=self._next_sequence(),
            source=self._source,
            cycle=self._cycles_completed,
        )
        if translated is not None:
            self._emit(translated)
        if self._market_event_observer is not None:
            self._market_event_observer(event)

    def _scanner_log(
        self,
        event_type: str,
        message: str,
        *,
        health: RuntimeHealthUpdate | None = None,
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

    def _scanner_error(self, message: str, error: Exception) -> None:
        self._scanner_log(
            "scanner_error",
            message,
            health=RuntimeHealthUpdate(
                market_data_status="FAILED",
                scanner_status="FAILED",
                last_error=type(error).__name__,
            ),
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
                last_error=type(exc).__name__,
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
        trading_ready: bool = False,
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
                    trading_environment=(
                        getattr(self._configuration, "trading", None).environment.value
                        if trading_ready
                        and getattr(self._configuration, "trading", None) is not None
                        else self._configuration.environment.value
                        if trading_ready
                        else None
                    ),
                    trading_rest_status="CONNECTED" if trading_ready else None,
                    orders_status="ENABLED" if trading_ready else None,
                    balances_status="CONNECTED" if trading_ready else None,
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


def _safe_runtime_error(error: Exception) -> str:
    detail = str(error).strip()
    upper = detail.upper()
    if any(
        marker in upper
        for marker in (
            "APP KEY", "APP_KEY", "SECRET", "TOKEN", "SIGNATURE",
            "AUTHORIZATION HEADER", "SESSION ID",
        )
    ):
        return type(error).__name__
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__

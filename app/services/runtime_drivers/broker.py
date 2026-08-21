"""Broker lifecycle driver for a desktop runtime session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging
from threading import Event, RLock, Thread
from time import monotonic

from app.broker_plugins import BrokerRuntime
from app.broker_plugins.webull.capabilities import map_webull_capabilities
from app.configuration import OperationalConfiguration
from app.live_execution.account_polling import (
    BrokerAccountSnapshot,
    poll_broker_account,
)
from app.market_data.models import MarketEvent, MarketEventType
from app.momentum_scanner import AssetClass
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeEventSink,
    RuntimeHealthUpdate,
    RuntimeWatchlistUpdate,
)
from app.operations.scanner_snapshot_publisher import ScannerSnapshotPublisher
from app.performance_diagnostics import performance_diagnostics
from app.services.market_event_translation import translate_market_event
from app.webull.client_factories import market_data_configuration
from app.webull.market_data_session import (
    MarketDataSession,
    current_market_data_session,
)


Clock = Callable[[], datetime]
AccountPoller = Callable[..., BrokerAccountSnapshot]
AccountSnapshotSink = Callable[[BrokerAccountSnapshot], None]
MarketEventTranslator = Callable[..., PaperRuntimeEvent | None]
MarketEventObserver = Callable[[MarketEvent], object]


_SCANNER_LOGGER = logging.getLogger("atlas.scanner")


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
        self._terminal_stream_failure_published = False
        self._scanner_pause_session: MarketDataSession | None = None
        self._scanner_configuration_changed = False
        self._capability_refresh_requested = False
        self._cycles_completed = 0
        self._scanner_events_since_observation = 0
        self._last_scanner_observation_at = 0.0
        self._last_scanner_detail_at = 0.0
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

        observer_start = getattr(self._market_event_observer, "start", None)
        if callable(observer_start):
            observer_start(self.environment)

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
            observer_stop = getattr(self._market_event_observer, "stop", None)
            if callable(observer_stop):
                observer_stop()
            self._publish(
                "BROKER_AUTHENTICATION_FAILED",
                "Configured broker authentication failed.",
                runtime_status="FAILED",
                broker_status="DISCONNECTED",
                last_error=_safe_runtime_error(exc),
                trading_auth_failed=True,
            )
            raise

        if self._startup_validator is not None:
            self._startup_validation = self._startup_validator.run()

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
        finally:
            try:
                self._stop_market_data()
            finally:
                try:
                    observer_stop = getattr(
                        self._market_event_observer, "stop", None,
                    )
                    if callable(observer_stop):
                        observer_stop()
                finally:
                    self._disconnect()

    def _start_market_data(self, stop_event: Event) -> None:
        if self._scanner is not None:
            self._scanner_log(
                "scanner_initialized",
                "Autonomous scanner components initialized.",
            )
        if (
            self._startup_validation is not None
            and not self._startup_validation.scanner_ready
        ):
            market_result = self._startup_validation.market_data
            reason = (
                getattr(self._startup_validation, "reason", None)
                or getattr(market_result, "reason", None)
                or "Scanner startup capabilities are unavailable."
            )
            self._scanner_log(
                "scanner_discovery_unavailable",
                str(reason),
                health=RuntimeHealthUpdate(
                    scanner_status="CAPABILITY_PAUSED",
                    last_warning=str(reason),
                ),
            )
            if (
                getattr(market_result, "reason", None)
                == "OVERNIGHT_ENTITLEMENT_REQUIRED"
            ):
                self._scanner_pause_session = MarketDataSession.OVERNIGHT
                # The capability probe connected the transport. Keep it up for
                # health visibility, but remember to close it at shutdown.
                self._market_data_connected = True
                return
            if self._scanner is not None:
                self._scanner.disconnect()
            elif self._market_data is not None:
                self._market_data.disconnect()
            return
        if self._market_data is None:
            if not self._configuration.market_data_streaming_enabled:
                self._publish_health(
                    "MARKET_DATA_DISABLED_BY_CONFIGURATION",
                    "Market-data streaming is disabled by configuration.",
                    RuntimeHealthUpdate(
                        market_data_status="DISABLED",
                        streaming_status="DISABLED",
                        scanner_status="DISABLED_BY_CONFIGURATION",
                    ),
                )
            else:
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
                        scanner_status="STOPPED",
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
                RuntimeHealthUpdate(
                    runtime_status="RUNNING",
                    market_data_status="CONNECTED",
                    streaming_status="CONNECTED",
                    subscription_status="ACCEPTED",
                ),
            )
        except Exception as exc:
            self._publish_terminal_market_data_failure(exc)
            self._market_data_stop.set()
            try:
                if self._scanner is not None:
                    self._scanner.disconnect()
                elif self._market_data is not None:
                    self._market_data.disconnect()
            except Exception:
                pass
            self._market_data_connected = False
            return

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
                    "WARMING" if trading.ready else "STOPPED"
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
            capability_paused = (
                str(getattr(market_data, "capability_state", ""))
                == "PARTIAL_CAPABILITY"
                and str(getattr(
                    getattr(market_data, "entitlement", None), "state", ""
                )) == "AVAILABLE"
            ) or str(getattr(market_data, "reason", "")) == (
                "OVERNIGHT_ENTITLEMENT_REQUIRED"
            )
            self._publish_health(
                "STARTUP_VALIDATION_FAILED",
                result.reason or "Startup validation failed.",
                RuntimeHealthUpdate(
                    scanner_status=(
                        "CAPABILITY_PAUSED" if capability_paused else "STOPPED"
                    ),
                    last_warning=result.reason or "Startup validation failed.",
                ),
            )

    def _start_scanner(self) -> bool:
        scanner = self._scanner
        assert scanner is not None
        observer_setter = getattr(scanner, "set_event_observer", None)
        if callable(observer_setter):
            observer_setter(self._handle_market_event)
        self._scanner_log(
            "universe_refresh_started",
            "Autonomous scanner universe discovery started.",
        )
        try:
            active_symbols = scanner.start(
                asset_classes=(AssetClass.STOCK,),
                force_reference_refresh=True,
            )
        except Exception as exc:
            try:
                scanner.disconnect()
            finally:
                self._scanner_log(
                    "universe_refresh_failed",
                    f"Scanner universe discovery failed: {type(exc).__name__}.",
                )
                self._scanner_error(
                    "Scanner initialization failed.",
                    exc,
                )
            raise

        self._market_data_connected = True
        warmup_snapshot = scanner.snapshot()
        performance_diagnostics.increment("scanner_snapshots_generated")
        universe_size = getattr(
            warmup_snapshot, "universe_size", len(active_symbols)
        )
        eligible_count = getattr(
            warmup_snapshot, "eligible_symbol_count", len(active_symbols)
        )
        # Compatibility snapshots predate the count fields and use their
        # default zeros. Active symbols remain authoritative in that case.
        if universe_size == 0 and active_symbols:
            universe_size = len(active_symbols)
        if eligible_count == 0 and active_symbols:
            eligible_count = len(active_symbols)
        self._scanner_log(
            "universe_refreshed",
            f"Scanner universe refresh completed: universe_size={universe_size}.",
        )
        self._scanner_log(
            "symbols_eligible",
            f"Scanner eligibility completed: eligible_symbol_count={eligible_count}; "
            f"reference_ready_count={len(active_symbols)}.",
        )
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
                "market_data_subscriptions",
                "Scanner market-data subscription count=0.",
            )
            self._scanner_log(
                "scanner_snapshot_published",
                "Published 0 ranked candidates from an empty scanner universe.",
            )
            self._scanner_log(
                "scanner_empty_fail_closed",
                reason,
                health=RuntimeHealthUpdate(
                    market_data_status="NO_SUPPORTED_SYMBOLS",
                    scanner_status="CAPABILITY_PAUSED",
                    supported_symbols=0,
                    subscription_symbols=(),
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
                scanner_status="WARMING",
                universe_status="LOADED",
                symbols_status="VALIDATED",
                reference_cache_status="WARM",
                ranking_status="ACTIVE",
                supported_symbols=len(active_symbols),
                subscription_symbols=tuple(sorted(active_symbols)),
            ),
        )
        self._scanner_log(
            "market_data_subscriptions",
            f"Scanner market-data subscription count={len(active_symbols)}.",
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
        quotes = getattr(getattr(result, "quotes", None), "state", "UNKNOWN")
        capability_state = str(getattr(result, "capability_state", "UNKNOWN"))
        current_session = str(getattr(result, "current_session", "CLOSED"))
        retry_at = getattr(result, "next_retry_at", None)
        overnight_paused = str(reason) == "OVERNIGHT_ENTITLEMENT_REQUIRED"
        retry_detail = (
            retry_at.isoformat()
            if isinstance(retry_at, datetime)
            else "the next premarket session"
        )
        no_supported_symbols = (
            str(reason) == "NO_SUPPORTED_SYMBOLS"
            or str(bars) == "UNSUPPORTED"
        )
        symbol_results = {
            item.symbol: str(item.result)
            for item in getattr(result, "symbol_results", ())
        }
        partial = (
            capability_state == "PARTIAL_CAPABILITY"
            and str(entitlement) == "AVAILABLE"
        )
        self._publish_health(
            "MARKET_DATA_PROBE_COMPLETED",
            "Market-data startup capability probe completed."
            if ready else str(reason),
            RuntimeHealthUpdate(
                market_data_status=(
                    "PARTIAL_CAPABILITY" if partial
                    else "READY" if ready
                    else "NO_SUPPORTED_SYMBOLS" if no_supported_symbols
                    else "STREAM_CONNECTED_SUBSCRIPTION_DENIED"
                    if str(streaming) == "AVAILABLE"
                    and str(subscription) == "UNAVAILABLE"
                    else "CONNECTED"
                    if str(reason) == "OVERNIGHT_ENTITLEMENT_REQUIRED"
                    else "DISABLED"
                ),
                market_data_environment=getattr(result, "environment", "UNKNOWN"),
                market_data_rest_status=(
                    "CONNECTED"
                    if str(endpoint) == "AVAILABLE" and str(bars) == "AVAILABLE"
                    else str(endpoint)
                ),
                historical_bars_status=(
                    "AVAILABLE" if str(bars) == "AVAILABLE" else str(bars)
                ),
                quotes_status=(
                    "AVAILABLE" if str(quotes) == "AVAILABLE" else str(quotes)
                ),
                streaming_status=(
                    "CONNECTED" if str(streaming) == "AVAILABLE" else str(streaming)
                ),
                subscription_status=(
                    "ACCEPTED" if str(subscription) == "AVAILABLE"
                    else "SUBSCRIPTION_REQUIRED"
                    if overnight_paused
                    else "DENIED"
                    if str(streaming) == "AVAILABLE" and str(subscription) in {
                        "UNAVAILABLE", "NOT_ENTITLED"
                    }
                    else str(subscription)
                ),
                heartbeat_status=(
                    "OK" if str(heartbeat) == "AVAILABLE" else str(heartbeat)
                ),
                reconnect_status=(
                    "READY" if str(reconnect) == "AVAILABLE" else str(reconnect)
                ),
                entitlement_status=(
                    "NOT_SUBSCRIBED"
                    if current_session == "OVERNIGHT"
                    and str(entitlement) == "NOT_ENTITLED"
                    else "CURRENT_SESSION_GRANTED"
                    if str(entitlement) == "AVAILABLE"
                    else str(entitlement)
                ),
                market_session_status=current_session,
                scanner_retry_status=(
                    retry_detail if overnight_paused else "ON_SESSION_CHANGE"
                    if not ready else "NOT_REQUIRED"
                ),
                scanner_status=(
                    "WARMING" if ready
                    else "CAPABILITY_PAUSED" if partial
                    else "CAPABILITY_PAUSED" if no_supported_symbols
                    else "CAPABILITY_PAUSED" if overnight_paused
                    else "STOPPED"
                ),
                probe_aapl_status=symbol_results.get("AAPL", "NOT_TESTED"),
                probe_spy_status=symbol_results.get("SPY", "NOT_TESTED"),
                probe_tsla_status=symbol_results.get("TSLA", "NOT_TESTED"),
                probe_msft_status=symbol_results.get("MSFT", "NOT_TESTED"),
                probe_nvda_status=symbol_results.get("NVDA", "NOT_TESTED"),
                last_warning=(
                    None if ready
                    else "Overnight market-data subscription unavailable. "
                    f"Automatic capability refresh: {retry_detail}."
                    if overnight_paused
                    else str(reason)
                ),
                capabilities=map_webull_capabilities(
                    self._broker_runtime.capabilities,
                    result,
                ),
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
                    performance_diagnostics.increment("scanner_evaluations")
                    self._scanner_events_since_observation += cycle.events_read
                    if cycle.events_read == 0:
                        self._publish_scanner_observation_if_due()
                        # An exhausted/nonblocking transport must not create a
                        # worker-side busy loop. This wait is interruptible and
                        # never occurs on the Qt thread.
                        self._market_data_stop.wait(0.01)
                        continue

                    snapshot = self._scanner.snapshot()
                    performance_diagnostics.increment(
                        "scanner_snapshots_generated"
                    )
                    now = self._timestamp()
                    stale = self._scanner_publisher.publish(
                        snapshot,
                        cycle=self._cycles_completed,
                        now=now,
                    )
                    if self._scanner_publisher.last_changed:
                        self._scanner_log(
                            "scanner_snapshot_published",
                            f"Published {len(snapshot.ranked_candidates)} "
                            "ranked candidates.",
                        )
                    self._publish_scanner_observation_if_due(force=False)
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

    def _publish_scanner_observation_if_due(self, *, force: bool = False) -> None:
        observed_at = monotonic()
        if (
            not force
            and self._last_scanner_observation_at
            and observed_at - self._last_scanner_observation_at < 1.0
        ):
            return
        events = self._scanner_events_since_observation
        self._scanner_events_since_observation = 0
        self._last_scanner_observation_at = observed_at
        self._scanner_log(
            "scanner_cycle",
            "Autonomous scanner evaluation interval completed.",
            health=RuntimeHealthUpdate(scanner_status="RUNNING"),
        )
        self._scanner_log(
            "events_consumed",
            f"Consumed {events} market events in the bounded interval.",
        )
        if observed_at - self._last_scanner_detail_at >= 10.0:
            self._last_scanner_detail_at = observed_at
            self._log_scanner_qualification_details()

    def _log_scanner_qualification_details(self) -> None:
        diagnostics = getattr(self._scanner, "diagnostic_results", None)
        if not callable(diagnostics):
            return
        aggregate = getattr(self._scanner, "qualification_diagnostics", None)
        if callable(aggregate):
            summary = aggregate(example_limit=3)
            if summary is not None:
                denominator = summary.complete
                percentages = ",".join(
                    f"{rule}:{(count * 100 / denominator):.1f}%"
                    if denominator
                    else f"{rule}:0.0%"
                    for rule, count in summary.rejection_counts
                )
                _SCANNER_LOGGER.info(
                    "event_type=scanner_qualification_summary evaluated=%s "
                    "complete=%s qualified=%s rejection_counts=%s "
                    "rejection_percentages=%s news_catalyst_states=%s "
                    "otherwise_qualified_with_catalyst=%s near_symbols=%s",
                    summary.evaluated,
                    summary.complete,
                    summary.qualified,
                    ",".join(
                        f"{rule}:{count}"
                        for rule, count in summary.rejection_counts
                    ),
                    percentages,
                    ",".join(
                        f"{status}:{count}"
                        for status, count in summary.catalyst_counts
                    ),
                    summary.otherwise_qualified_with_catalyst,
                    ",".join(summary.near_qualified_symbols) or "--",
                )
        for result, decision in diagnostics(limit=3):
            state = result.state
            observation = result.observation
            if observation is None:
                _SCANNER_LOGGER.info(
                    "event_type=scanner_qualification symbol=%s status=incomplete "
                    "missing=%s quote_timestamp=%s trade_timestamp=%s "
                    "snapshot_timestamp=%s",
                    state.symbol,
                    ",".join(result.missing_fields) or "--",
                    _iso_or_dash(state.quote_timestamp),
                    _iso_or_dash(state.trade_timestamp),
                    _iso_or_dash(state.snapshot_timestamp),
                )
                continue
            assert decision is not None
            _SCANNER_LOGGER.info(
                "event_type=scanner_qualification symbol=%s status=%s "
                "price=%s previous_close=%s current_volume=%s "
                "average_30_day_volume=%s float_shares=%s bid=%s ask=%s "
                "catalyst=%s news_catalyst=%s tradable=%s halted=%s "
                "percentage_change=%s "
                "relative_volume=%s dollar_volume=%s spread_percent=%s "
                "failed_rules=%s",
                observation.symbol,
                "qualified" if decision.qualified else "rejected",
                observation.price,
                observation.previous_close,
                observation.current_volume,
                observation.average_30_day_volume,
                observation.float_shares,
                observation.bid,
                observation.ask,
                observation.catalyst.value,
                observation.catalyst_status.value,
                observation.tradable,
                observation.halted,
                decision.metrics.percentage_change,
                decision.metrics.relative_volume,
                decision.metrics.dollar_volume,
                decision.metrics.spread_percent,
                ",".join(decision.failed_rules) or "--",
            )
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
            self._capability_refresh_requested = True
            self._observe_probe_success("STREAM_RECONNECT")
            self._publish_health(
                "MARKET_DATA_RECONNECTED",
                "Reconnected to Webull market data.",
                RuntimeHealthUpdate(
                    market_data_status="CONNECTED",
                    streaming_status="CONNECTED",
                    reconnect_attempts=attempt,
                ),
            )
        elif lifecycle == "parse_failed":
            self._publish_health(
                "MARKET_DATA_PARSE_FAILED",
                "Webull market-data payload could not be decoded.",
                RuntimeHealthUpdate(
                    market_data_status="STREAM_PARTIALLY_DEGRADED",
                    streaming_status="STREAM_PARTIALLY_DEGRADED",
                    market_data_rest_status="AVAILABLE",
                    last_error=detail,
                    reconnect_attempts=attempt,
                ),
            )
        elif lifecycle == "decode_recovered":
            self._publish_health(
                "MARKET_DATA_DECODER_RECOVERED",
                "Webull market-data payload decoding recovered.",
                RuntimeHealthUpdate(
                    market_data_status="STREAM_CONNECTED",
                    streaming_status="STREAM_CONNECTED",
                    market_data_rest_status="AVAILABLE",
                    last_warning=None,
                ),
            )
        elif lifecycle == "decode_threshold_exceeded":
            self._publish_health(
                "MARKET_DATA_DECODE_THRESHOLD_EXCEEDED",
                "Webull market-data decode failure threshold was exceeded.",
                RuntimeHealthUpdate(
                    market_data_status="STREAM_FAILED",
                    streaming_status="STREAM_FAILED",
                    market_data_rest_status="AVAILABLE",
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
            "Webull market-data streaming failed; REST market data remains available.",
            RuntimeHealthUpdate(
                runtime_status="DEGRADED",
                market_data_status="REST_ONLY",
                market_data_rest_status="AVAILABLE",
                streaming_status=_stream_failure_classification(error),
                scanner_status="STOPPED",
                last_warning=(
                    f"{type(error).__name__}: {error}"
                    if str(error)
                    else type(error).__name__
                ),
            ),
        )

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
        performance_diagnostics.increment("market_events_processed")
        translated = self._market_event_translator(
            event,
            sequence=self._next_sequence(),
            source=self._source,
            cycle=self._cycles_completed,
        )
        if translated is not None and event.event_type is MarketEventType.QUOTE:
            translated = replace(
                translated,
                event_type="MARKET_DATA_QUOTE_RECEIVED",
                health=RuntimeHealthUpdate(
                    runtime_status="RUNNING",
                    market_data_status="CONNECTED",
                    streaming_status="CONNECTED",
                    subscription_status="ACCEPTED",
                    quotes_status="AVAILABLE",
                    last_warning=None,
                ),
            )
            self._observe_probe_success("STREAM_QUOTE")
        elif translated is not None and event.event_type is MarketEventType.SESSION_CHANGE:
            translated = replace(
                translated,
                health=RuntimeHealthUpdate(
                    runtime_status="RUNNING",
                    market_data_status="CONNECTED",
                    streaming_status="CONNECTED",
                    subscription_status="ACCEPTED",
                    last_warning=None,
                ),
            )
            self._observe_probe_success("SESSION_TRANSITION")
            self._capability_refresh_requested = True
        elif translated is not None:
            # Any translated event is proof that registration, transport, and
            # payload decoding are currently working.  Quote availability is
            # kept quote-specific, but stream health is payload-agnostic.
            translated = replace(
                translated,
                health=RuntimeHealthUpdate(
                    runtime_status="RUNNING",
                    market_data_status="CONNECTED",
                    streaming_status="CONNECTED",
                    subscription_status="ACCEPTED",
                    last_warning=None,
                ),
            )
            self._observe_probe_success("STREAM_PAYLOAD")
        elif event.event_type is MarketEventType.BOOK_SNAPSHOT:
            # A decoded book snapshot is authoritative stream activity even
            # though it has no trading/read-model translation of its own.
            self._publish_health(
                "MARKET_DATA_SNAPSHOT_RECEIVED",
                f"Decoded live market-data snapshot for {event.symbol or '--'}.",
                RuntimeHealthUpdate(
                    runtime_status="RUNNING",
                    market_data_status="CONNECTED",
                    streaming_status="CONNECTED",
                    subscription_status="ACCEPTED",
                    last_warning=None,
                ),
                timestamp=event.timestamp,
            )
            self._observe_probe_success("STREAM_PAYLOAD")
        elif event.event_type is MarketEventType.SESSION_CHANGE:
            self._observe_probe_success("SESSION_TRANSITION")
            self._capability_refresh_requested = True
        if translated is not None:
            self._emit(translated)
        if self._market_event_observer is not None:
            self._market_event_observer(event)

    def _observe_probe_success(self, capability: str) -> None:
        if self._market_data_probe is None:
            return
        observer = getattr(self._market_data_probe, "observation_succeeded", None)
        if callable(observer):
            observer(capability)

    def _scanner_log(
        self,
        event_type: str,
        message: str,
        *,
        health: RuntimeHealthUpdate | None = None,
    ) -> None:
        _SCANNER_LOGGER.info(
            "event_type=%s cycle=%d message=%s",
            event_type,
            self._cycles_completed,
            message,
        )
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
                scanner_status="STOPPED",
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
            self._publish_health(
                "BROKER_REST_OBSERVED",
                "Broker account, balances, positions, and orders loaded.",
                RuntimeHealthUpdate(
                    runtime_status="RUNNING",
                    broker_status="CONNECTED",
                    trading_rest_status="CONNECTED",
                    account_status="AVAILABLE",
                    buying_power_status="AVAILABLE",
                    positions_status="AVAILABLE",
                    orders_status="AVAILABLE",
                    balances_status="AVAILABLE",
                    last_error=None,
                ),
            )
            self._cycles_completed += 1
            cycle_sink(self._cycles_completed)
            self._retry_scanner_after_session_transition(stop_event)
            if stop_event.wait(interval_seconds):
                break

    def _retry_scanner_after_session_transition(self, stop_event: Event) -> None:
        if self._market_data_probe is None:
            return
        session = current_market_data_session(self._clock)
        session_changed = (
            self._scanner_pause_session is not None
            and session is not self._scanner_pause_session
        )
        if not (
            session_changed
            or self._scanner_configuration_changed
            or self._capability_refresh_requested
        ):
            return
        invalidate = getattr(
            self._market_data_probe,
            "configuration_changed",
            None,
        )
        if callable(invalidate) and (
            self._scanner_configuration_changed
            or self._capability_refresh_requested
        ):
            invalidate()
        self._scanner_configuration_changed = False
        self._capability_refresh_requested = False
        result = self._market_data_probe.run()
        self._publish_probe_result(result)
        if not result.scanner_ready or self._scanner_pause_session is None:
            return
        self._scanner_pause_session = None
        self._startup_validation = None
        self._start_market_data(stop_event)

    def market_data_configuration_changed(self) -> None:
        """Request one scanner capability retry after an operator config edit."""

        if self._market_data_probe is None:
            return
        invalidate = getattr(
            self._market_data_probe, "configuration_changed", None
        )
        if callable(invalidate):
            invalidate()
        self._scanner_configuration_changed = True

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
        trading_auth_failed: bool = False,
    ) -> None:
        timestamp = self._timestamp()
        trading_section = getattr(self._configuration, "trading", None)
        trading_environment = (
            (
                trading_section.environment.value
                if trading_section is not None
                else self._configuration.environment.value
            )
            if trading_ready or trading_auth_failed
            else None
        )
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
                    trading_environment=trading_environment,
                    trading_rest_status=(
                        "CONNECTED" if trading_ready
                        else "AUTH_FAILED" if trading_auth_failed
                        else None
                    ),
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
        *,
        timestamp: datetime | None = None,
    ) -> None:
        self._emit(
            PaperRuntimeEvent(
                sequence=self._next_sequence(),
                timestamp=timestamp or self._timestamp(),
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


def _stream_failure_classification(error: Exception) -> str:
    current: BaseException | None = error
    for _ in range(8):
        if current is None:
            break
        detail = str(current).upper()
        if (
            "PROTOCOL NOT SUPPORTED" in detail
            or "UNACCEPTABLE PROTOCOL VERSION" in detail
        ):
            return "PROTOCOL_UNSUPPORTED"
        current = current.__cause__ or current.__context__
    return "UNAVAILABLE"


def _iso_or_dash(value: datetime | None) -> str:
    return "--" if value is None else value.isoformat()

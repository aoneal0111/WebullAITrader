"""Configuration-aware broker composition for the desktop runtime."""

from __future__ import annotations

from collections.abc import Callable
import logging

from app.broker_plugins import BrokerRuntime, create_broker_runtime
from app.catalysts import (
    CatalystAggregator,
    build_catalyst_providers,
)
from app.composition.desktop_infrastructure import (
    create_desktop_scanner_infrastructure,
)
from app.configuration import OperationalConfiguration, load_configuration
from app.live_execution.account_polling import (
    BrokerAccountSnapshot,
    poll_broker_account,
)
from app.live_execution.broker_factory import (
    build_webull_broker,
    build_webull_market_data_stream,
)
from app.operations.runtime import RuntimeEventSink
from app.paper_trade_experiment import (
    PaperTradeExperimentJournal,
    PaperTradeExperimentWorker,
)
from app.reference_data import ReferenceDataCache, ReferenceDataService
from app.scanner_adapter import (
    MarketEventScannerAdapter,
    ScannerReferenceData,
    ScannerReferenceStore,
)
from app.services.chart_market_data import ChartMarketDataService
from app.services.runtime_drivers.broker import (
    Clock,
    DesktopBrokerRuntimeDriver,
    utc_now,
)
from app.universe import UniverseService
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    WebullScannerReferenceProvider,
    WebullScannerUniverseProvider,
)
from app.webull.client_factories import (
    MarketDataClientFactory,
    market_data_cache_scope,
    market_data_configuration,
)
from app.webull.market_data_probe import MarketDataCapabilityProbe
from app.webull.startup_validation import RuntimeStartupValidator
from app.webull.client_factories import trading_configuration
from app.webull.request_audit import AuditedMarketDataClient, RequestIsolationGuard


ConfigurationLoader = Callable[[], OperationalConfiguration]
BrokerRuntimeFactory = Callable[..., BrokerRuntime]


_SCANNER_LOGGER = logging.getLogger("atlas.scanner")


class _ResearchFanoutDecisionSink:
    """Publish authoritative decisions to research without sharing lifecycle ownership."""

    def __init__(self, primary: object | None, research: Callable[[object], object]) -> None:
        self.primary = primary
        self.research = research

    def __call__(self, decision: object) -> None:
        if callable(self.primary):
            self.primary(decision)
        try:
            self.research(decision)
        except Exception:
            pass

    def reset_symbol(self, symbol: str) -> None:
        primary_reset = getattr(self.primary, "reset_symbol", None)
        if callable(primary_reset):
            primary_reset(symbol)
        research_owner = getattr(self.research, "__self__", None)
        research_reset = getattr(research_owner, "reset_symbol", None)
        if callable(research_reset):
            research_reset(symbol)

    def close(self) -> None:
        primary_close = getattr(self.primary, "close", None)
        if callable(primary_close):
            primary_close()


def create_configured_desktop_broker_driver(
    *,
    event_sink: RuntimeEventSink,
    account_snapshot_sink: Callable[[BrokerAccountSnapshot], None],
    configuration_loader: ConfigurationLoader = load_configuration,
    broker_runtime_factory: BrokerRuntimeFactory = create_broker_runtime,
    webull_broker_factory: Callable[[object], object] = build_webull_broker,
    webull_market_data_factory: Callable[
        [object], object
    ] = build_webull_market_data_stream,
    account_poller: Callable[..., BrokerAccountSnapshot] = poll_broker_account,
    market_event_observer: Callable[[object], object] | None = None,
    clock: Clock = utc_now,
    source: str = "desktop-broker-runtime",
) -> DesktopBrokerRuntimeDriver:
    """Load configuration and resolve its broker through the plugin registry."""

    if not callable(configuration_loader):
        raise TypeError("configuration_loader must be callable")
    if not callable(broker_runtime_factory):
        raise TypeError("broker_runtime_factory must be callable")
    if not callable(webull_broker_factory):
        raise TypeError("webull_broker_factory must be callable")
    if not callable(webull_market_data_factory):
        raise TypeError("webull_market_data_factory must be callable")
    if not callable(account_snapshot_sink):
        raise TypeError("account_snapshot_sink must be callable")
    if not callable(account_poller):
        raise TypeError("account_poller must be callable")
    if (
        market_event_observer is not None
        and not callable(market_event_observer)
    ):
        raise TypeError(
            "market_event_observer must be callable or None"
        )

    configuration = configuration_loader()
    if not isinstance(configuration, OperationalConfiguration):
        raise TypeError(
            "configuration_loader must return OperationalConfiguration"
        )

    runtime_market_data_factory = webull_market_data_factory
    if webull_market_data_factory is build_webull_market_data_stream:
        runtime_market_data_factory = lambda value: build_webull_market_data_stream(
            value, clock=clock
        )
    broker_runtime = broker_runtime_factory(
        provider=configuration.broker_provider,
        configuration=configuration,
        webull_broker_factory=webull_broker_factory,
        webull_market_data_factory=runtime_market_data_factory,
    )

    market_data_configuration_value = market_data_configuration(configuration)
    request_guard = RequestIsolationGuard(
        trading_configuration(configuration), market_data_configuration_value
    )
    data_client = LazyOfficialDataClient(
        lambda: AuditedMarketDataClient(
            MarketDataClientFactory(market_data_configuration_value).create(),
            request_guard,
            market_data_configuration_value,
        )
    )
    warrior_history_service = ChartMarketDataService(
        data_client,
        bar_count=120,
    )

    scanner_coordinator = None
    if broker_runtime.market_data is not None:
        experiment_decision_sink = None
        experiment_execution_environment = trading_configuration(
            configuration
        ).environment.value
        if (
            experiment_execution_environment in {"PAPER", "TEST"}
            and not configuration.live_trading_enabled
        ):
            experiment_path = configuration.execution_database_path.with_name(
                "paper_trade_experiment.sqlite3"
            )
            experiment_decision_sink = PaperTradeExperimentWorker(
                experiment_path,
                execution_environment=experiment_execution_environment,
                journal_factory=PaperTradeExperimentJournal,
            )
        research_decision_sink = getattr(
            market_event_observer, "observe_scanner_decision", None,
        )
        scanner_decision_sink = experiment_decision_sink
        if callable(research_decision_sink):
            scanner_decision_sink = _ResearchFanoutDecisionSink(
                experiment_decision_sink, research_decision_sink,
            )
        universe_provider = WebullScannerUniverseProvider(
            data_client,
            clock=clock,
        )
        catalyst_providers = build_catalyst_providers(data_client, configuration)
        reference_provider = WebullScannerReferenceProvider(
            data_client,
            universe_provider,
            clock=clock,
            environment=market_data_configuration_value.environment.value,
            identity_scope=market_data_cache_scope(
                market_data_configuration_value
            )[1],
            catalyst_aggregator=CatalystAggregator(
                catalyst_providers,
                clock=clock,
            ),
        )
        reference_store = ScannerReferenceStore()

        def store_reference(record) -> None:
            reference_store.put(
                ScannerReferenceData(
                    symbol=record.symbol,
                    previous_close=record.previous_close,
                    average_30_day_volume=record.average_30_day_volume,
                    float_shares=record.float_shares,
                    float_provenance=record.float_provenance,
                    catalyst=record.catalyst,
                    catalyst_headline=record.catalyst_headline,
                    catalyst_status=record.catalyst_status,
                    tradable=record.tradable,
                    updated_at=record.as_of,
                    current_volume=record.current_volume,
                    catalyst_source=record.catalyst_source,
                    catalyst_published_at=record.catalyst_published_at,
                    catalyst_source_url=record.catalyst_source_url,
                    corroborating_sources=record.corroborating_sources,
                    catalyst_evidence_count=record.catalyst_evidence_count,
                    catalyst_event_count=record.catalyst_event_count,
                )
            )

            needs_history = getattr(
                market_event_observer,
                "needs_historical_preload",
                None,
            )
            preload_history = getattr(
                market_event_observer,
                "preload_historical_bars",
                None,
            )

            if (
                callable(needs_history)
                and callable(preload_history)
                and needs_history(record.symbol)
            ):
                bars = warrior_history_service.load_historical_bars(
                    record.symbol,
                    "1M",
                )
                preload_history(record.symbol, bars)

        scanner_adapter = MarketEventScannerAdapter(
            reference_store,
            price_observer=(
                None if experiment_decision_sink is None else
                experiment_decision_sink.observe_price
            ),
        )
        scanner_infrastructure = create_desktop_scanner_infrastructure(
            market_data_client=broker_runtime.market_data,
            universe_service=UniverseService(universe_provider),
            reference_data_service=ReferenceDataService(
                reference_provider,
                cache=ReferenceDataCache(
                    scope=market_data_cache_scope(
                        market_data_configuration_value
                    )
                ),
            ),
            scanner_adapter=scanner_adapter,
            reference_sink=store_reference,
            clock=clock,
            # Bound one production drain so ranking snapshots and lifecycle
            # telemetry are published promptly on an active multi-symbol feed.
            maximum_events_per_cycle=100,
            scanner_decision_sink=(
                scanner_decision_sink
            ),
        )
        scanner_coordinator = scanner_infrastructure.coordinator
        binder = getattr(market_event_observer, "bind_scanner_adapter", None)
        if callable(binder):
            binder(scanner_adapter)
        decision_binder = getattr(
            market_event_observer, "bind_scanner_decision_source", None,
        )
        if callable(decision_binder):
            decision_binder(
                scanner_infrastructure.pipeline.latest_decision,
                lambda symbol: any(
                    item.symbol == symbol.strip().upper()
                    for item in scanner_infrastructure.pipeline.ranked()
                ),
            )
        retained = getattr(market_event_observer, "retained_symbols", None)
        if callable(retained):
            scanner_coordinator.set_retained_channels_source(retained)

    # Capability probing must not mutate the scanner's live subscription
    # session. Use an independent stream for startup capability checks.
    market_data_probe_stream = runtime_market_data_factory(
        configuration
    )
    market_data_probe = MarketDataCapabilityProbe(
        market_data_configuration_value,
        data_client,
        market_data_probe_stream,
        clock=clock,
    )
    startup_validator = RuntimeStartupValidator(
        broker_runtime.execution,
        trading_configuration(configuration),
        market_data_probe,
    )

    return DesktopBrokerRuntimeDriver(
        configuration=configuration,
        broker_runtime=broker_runtime,
        event_sink=event_sink,
        account_snapshot_sink=account_snapshot_sink,
        account_poller=account_poller,
        market_event_observer=market_event_observer,
        scanner_coordinator=scanner_coordinator,
        market_data_probe=market_data_probe,
        startup_validator=startup_validator,
        clock=clock,
        source=source,
    )


__all__ = [
    "DesktopBrokerRuntimeDriver",
    "create_configured_desktop_broker_driver",
]

"""Configuration-aware broker composition for the desktop runtime."""

from __future__ import annotations

from collections.abc import Callable

from app.broker_plugins import BrokerRuntime, create_broker_runtime
from app.catalysts import (
    CatalystAggregator,
    SECEdgarCatalystProvider,
    SECEdgarPolicy,
    WebullCatalystProvider,
    log_sec_edgar_provider_state,
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
        universe_provider = WebullScannerUniverseProvider(
            data_client,
            clock=clock,
        )
        catalyst_providers = [WebullCatalystProvider(data_client)]
        if configuration.sec_edgar is None:
            log_sec_edgar_provider_state(enabled=False)
        if configuration.sec_edgar is not None:
            catalyst_providers.append(
                SECEdgarCatalystProvider(
                    SECEdgarPolicy(
                        user_agent=configuration.sec_edgar.user_agent,
                        freshness_days=configuration.sec_edgar.freshness_days,
                        timeout_seconds=configuration.sec_edgar.timeout_seconds,
                    )
                )
            )
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
                    catalyst=record.catalyst,
                    catalyst_headline=record.catalyst_headline,
                    catalyst_status=record.catalyst_status,
                    tradable=record.tradable,
                    updated_at=record.as_of,
                    current_volume=record.current_volume,
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

        scanner_adapter = MarketEventScannerAdapter(reference_store)
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
        )
        scanner_coordinator = scanner_infrastructure.coordinator
        binder = getattr(market_event_observer, "bind_scanner_adapter", None)
        if callable(binder):
            binder(scanner_adapter)
        retained = getattr(market_event_observer, "retained_symbols", None)
        if callable(retained):
            scanner_coordinator.set_retained_channels_source(retained)

    market_data_probe = MarketDataCapabilityProbe(
        market_data_configuration_value,
        data_client,
        scanner_coordinator or broker_runtime.market_data,
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




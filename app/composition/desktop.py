from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.configuration import load_configuration
from app.services.chart_market_data import ChartMarketDataService
from app.operations_core import ApplicationStateStore, OperationsBus
from app.order_cancellation import OrderCancellationRuntime
from app.order_placement import OrderPlacementRuntime
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.execution_engine import PaperExecutionEngine
from app.services import OrderCommandFactory, RuntimeService, TradingService
from app.operations.runtime import PaperRuntimeEvent, RuntimeHealthUpdate
from app.webull.client_factories import (
    MarketDataClientFactory,
    market_data_configuration,
    trading_configuration,
)
from app.webull.request_audit import AuditedMarketDataClient, RequestIsolationGuard
from app.webull.sdk_market_data import LazyOfficialDataClient
from app.webull.market_data_session import utc_now
from app.strategies.warrior_momentum.desktop_sidecar import (
    CompositeMarketEventObserver, WarriorDesktopSidecar,
    strategy_configuration_fingerprint,
)
from app.strategies.warrior_momentum.forward_models import PaperAccountContext
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.execution_quote import WebullExecutionQuoteSource
from app.strategies.warrior_momentum.forward_runtime import management_context_available
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver
from app.entry_opportunity_value import EntryOpportunityValueRuntimeObserver
from app.adaptive_entry_research import AdaptiveWorkingEntryObserver
from app.memory_observability import MemoryObservability
from app.performance_diagnostics import performance_diagnostics
from app.crypto_research import (
    CryptoCatalystCollectionConfig,
    CryptoCatalystCollectionSink,
    CryptoIntelligenceResearchRuntime,
    CryptoCatalystAcquisitionRuntime,
    adapt_catalyst_provider,
    set_catalyst_provider_enabled,
    CryptoPair,
    CryptoResearchRuntime,
    WebullCryptoResearchProvider,
)

from .desktop_runtime import create_desktop_runtime_service
from .desktop_runtime_config import DesktopRuntimeConfiguration
from .runtime_projection_pipeline import (
    RuntimeProjectionPipeline,
    create_runtime_projection_pipeline,
)
from app.paper_trading.command_composition import (
    PAPER_ACCOUNT_ID,
    PaperTradingCommandComposition,
    create_paper_trading_command_composition,
)
from app.portfolio_intelligence import PortfolioAccount, PortfolioIntelligenceService, PortfolioRiskLimits, load_portfolio_intelligence_configuration


@dataclass(slots=True)
class DesktopComposition:
    bus: OperationsBus
    state_store: ApplicationStateStore
    runtime_service: RuntimeService
    trading_service: TradingService | None = None
    order_command_factory: OrderCommandFactory | None = None
    paper_order_book: PaperOrderBook | None = None
    paper_execution_engine: PaperExecutionEngine | None = None
    paper_trading_commands: PaperTradingCommandComposition | None = None
    runtime_projections: RuntimeProjectionPipeline | None = None
    chart_market_data_service: ChartMarketDataService | None = None
    chart_default_symbol: str | None = None
    warrior_forward_sidecar: WarriorDesktopSidecar | None = None
    autonomous_paper_bridge: AutonomousPaperExecutionBridge | None = None
    trade_intelligence_observer: TradeIntelligenceRuntimeObserver | None = None
    entry_opportunity_value_observer: EntryOpportunityValueRuntimeObserver | None = None
    adaptive_entry_research_observer: AdaptiveWorkingEntryObserver | None = None
    crypto_research_runtime: CryptoResearchRuntime | None = None
    crypto_intelligence_runtime: CryptoIntelligenceResearchRuntime | None = None
    crypto_catalyst_acquisition_runtime: CryptoCatalystAcquisitionRuntime | None = None
    memory_observability: MemoryObservability | None = None

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        """Close composed resources in lifecycle order."""

        crypto = self.crypto_research_runtime
        if crypto is not None:
            try:
                crypto.close(timeout_seconds=min(timeout_seconds, 2.0))
            except Exception:
                # Research has no authority over application shutdown.
                pass
        acquisition = self.crypto_catalyst_acquisition_runtime
        if acquisition is not None:
            try:
                acquisition.close(timeout_seconds=min(timeout_seconds, 2.0))
            except Exception:
                pass
        intelligence = self.crypto_intelligence_runtime
        if intelligence is not None:
            try:
                intelligence.close(timeout_seconds=min(timeout_seconds, 2.0))
            except Exception:
                pass
        diagnostics = self.memory_observability
        if diagnostics is not None and diagnostics.enabled:
            try:
                diagnostics.record_lifecycle("shutdown")
            except Exception:
                pass
            try:
                diagnostics.sample()
            except Exception:
                pass
            try:
                diagnostics.close(timeout_seconds=min(timeout_seconds, 2.0))
            except Exception:
                # Diagnostics have no authority over production shutdown.
                pass
        runtime_stopped = self.runtime_service.close(
            timeout_seconds=timeout_seconds
        )
        if self.paper_trading_commands is not None:
            self.paper_trading_commands.close()
        if self.warrior_forward_sidecar is not None:
            self.warrior_forward_sidecar.stop()
        if self.trade_intelligence_observer is not None:
            self.trade_intelligence_observer.stop(timeout_seconds=timeout_seconds)
        self.state_store.close()
        return runtime_stopped


def create_desktop_composition(
    driver_factory: Callable[[], object] | None = None,
    *,
    configuration: DesktopRuntimeConfiguration = DesktopRuntimeConfiguration(),
    placement_runtime: OrderPlacementRuntime | None = None,
    cancellation_runtime: OrderCancellationRuntime | None = None,
    order_command_factory: OrderCommandFactory | None = None,
    paper_order_book: PaperOrderBook | None = None,
    paper_persistence_path: str | Path | None = None,
    paper_clock: Callable[[], datetime] | None = None,
    catalyst_providers: Sequence[object] = (),
) -> DesktopComposition:
    """Construct the desktop application dependency graph."""

    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)

    if (placement_runtime is None) != (cancellation_runtime is None):
        raise ValueError(
            "placement_runtime and cancellation_runtime must be provided together"
        )

    operational_configuration = load_configuration()
    trade_intelligence_observer = TradeIntelligenceRuntimeObserver(
        enabled=(
            operational_configuration.trade_intelligence_enabled
            and not operational_configuration.live_trading_enabled
        ),
        environment=operational_configuration.environment.value,
        path=operational_configuration.trade_intelligence_path,
        capacity=operational_configuration.trade_intelligence_queue_capacity,
    )
    chart_market_configuration = market_data_configuration(
        operational_configuration
    )
    chart_request_guard = RequestIsolationGuard(
        trading_configuration(operational_configuration),
        chart_market_configuration,
    )
    # Subscriptions and execution permissions are not chart selections. Atlas
    # candidates and explicit operator interaction own chart focus.
    chart_default_symbol = None
    def portfolio_account_source() -> PortfolioAccount:
        state = state_store.snapshot()
        account = state.broker_account
        if account is not None:
            return PortfolioAccount(account.account_id, account.equity, account.cash_balance, account.buying_power, account.currency)
        paper = state.paper_runtime
        return PortfolioAccount(
            operational_configuration.account_id or PAPER_ACCOUNT_ID,
            paper.current_equity if paper is not None else None,
            None,
            None,
        )

    runtime_projections = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id=(
            operational_configuration.account_id
            or PAPER_ACCOUNT_ID
        ),
        watchlist_stale_after=timedelta(
            seconds=(
                operational_configuration.maximum_market_data_age_seconds
            )
        ),
        portfolio_account_source=portfolio_account_source,
        portfolio_intelligence_service=PortfolioIntelligenceService(
            configuration=load_portfolio_intelligence_configuration(),
            limits=PortfolioRiskLimits(
                maximum_open_positions=operational_configuration.max_open_positions,
            )
        ),
    )
    trade_intelligence_observer.bind_authoritative_focus_sources(
        position_source=lambda: runtime_projections.position_projection.snapshot,
        order_source=lambda: runtime_projections.order_projection.snapshot,
    )
    chart_observation_sequence = 0

    def publish_chart_observation(event_type: str, symbol: str, count: int) -> None:
        nonlocal chart_observation_sequence
        chart_observation_sequence += 1
        runtime_projections.sink(PaperRuntimeEvent(
            sequence=chart_observation_sequence,
            timestamp=utc_now(),
            event_type=event_type,
            message=f"Loaded {count} historical bars for {symbol} through REST.",
            cycle=0,
            symbol=symbol,
            source="atlas-chart-rest",
            health=RuntimeHealthUpdate(
                market_data_status="CONNECTED",
                market_data_rest_status="CONNECTED",
                historical_bars_status="AVAILABLE",
            ),
        ))

    shared_rest_market_data = LazyOfficialDataClient(
        lambda: AuditedMarketDataClient(
            MarketDataClientFactory(chart_market_configuration).create(),
            chart_request_guard,
            chart_market_configuration,
        )
    )
    chart_market_data_service = ChartMarketDataService(
        shared_rest_market_data,
        observation_sink=publish_chart_observation,
    )
    execution_quote_source = WebullExecutionQuoteSource(shared_rest_market_data)

    def position_average_cost(symbol: str) -> Decimal | None:
        position = runtime_projections.position_projection.position_for_symbol(
            symbol,
        )
        return (
            None
            if position is None
            else Decimal(position.average_cost)
        )

    def position_quantity(symbol: str) -> Decimal:
        position = runtime_projections.position_projection.position_for_symbol(
            symbol,
        )
        return (
            Decimal("0")
            if position is None
            else Decimal(position.quantity)
        )

    def adaptive_position(symbol: str) -> tuple[Decimal, datetime]:
        position = runtime_projections.position_projection.position_for_symbol(
            symbol,
        )
        if position is None:
            return Decimal("0"), utc_now()
        return Decimal(position.quantity), position.updated_at

    paper_trading_commands = None
    market_event_observer = None
    if placement_runtime is None:
        def paper_runtime_event_sink(event: PaperRuntimeEvent) -> None:
            runtime_projections.sink(event)
            symbol = event.symbol
            if symbol is None:
                return
            order_id = None if event.order is None else event.order.order_id
            lifecycle_id = None
            if order_id is not None and paper_order_book is not None:
                try:
                    lifecycle_id = paper_order_book.get(order_id).request.strategy_lifecycle_id
                except Exception:
                    lifecycle_id = None
            fill = event.fill
            trade_intelligence_observer.observe_paper_fact(
                observation_id=f"{event.source}:{event.sequence}:{event.event_type}",
                observed_at=event.timestamp, event_type=event.event_type,
                symbol=symbol, order_id=order_id,
                fill_id=None if fill is None else fill.request_id,
                side=(
                    event.order.side if event.order is not None
                    else None if fill is None else fill.side
                ),
                price=None if fill is None else fill.fill_price,
                quantity=(
                    Decimal(event.order.quantity) if event.order is not None
                    else None if fill is None else fill.quantity
                ),
                strategy_lifecycle_id=lifecycle_id,
            )
        paper_trading_commands = create_paper_trading_command_composition(
            order_book=paper_order_book,
            event_sink=paper_runtime_event_sink,
            persistence_path=paper_persistence_path,
            position_average_cost_source=position_average_cost,
            position_quantity_source=position_quantity,
            clock=paper_clock,
        )
        placement_runtime = paper_trading_commands.placement_runtime
        cancellation_runtime = paper_trading_commands.cancellation_runtime
        order_command_factory = (
            order_command_factory
            or paper_trading_commands.order_command_factory
        )
        paper_order_book = paper_trading_commands.order_book
        market_event_observer = (
            paper_trading_commands.gateway.process_market_event
        )

    def warrior_account_context() -> PaperAccountContext | None:
        state = state_store.snapshot()
        account = state.broker_account
        if account is not None:
            equity = getattr(account, "equity", None)
            buying_power = getattr(account, "buying_power", None)
        else:
            paper = state.paper_runtime
            equity = None if paper is None else paper.current_equity
            buying_power = equity
        if equity is None or buying_power is None:
            return None
        return PaperAccountContext(
            equity=Decimal(equity), buying_power=Decimal(buying_power),
            allowed_symbols=frozenset(operational_configuration.allowed_symbols),
            risk_engine_approved=True, broker_restriction=False,
            symbol_authorization_mode=(
                operational_configuration.paper_symbol_authorization_mode
            ),
        )

    autonomous_paper_bridge = None
    if paper_trading_commands is not None:
        autonomous_paper_bridge = AutonomousPaperExecutionBridge(
            paper_trading_commands.trading_service,
            paper_trading_commands.order_command_factory,
            mode=configuration.runtime_mode.value,
            enabled=operational_configuration.warrior_forward_paper_enabled,
            order_book=paper_trading_commands.order_book,
            position_quantity_source=position_quantity,
            management_context_source=lambda symbol: management_context_available(
                operational_configuration.warrior_forward_capture_path, symbol,
                configuration_fingerprint=strategy_configuration_fingerprint(),
                allow_compatible_generation=True,
            ),
        )
        autonomous_paper_bridge.begin_reconciliation()
        autonomous_paper_bridge.reconcile()

    def eov_order_correlation(lifecycle_id: str) -> dict[str, object] | None:
        if paper_order_book is None:
            return None
        try:
            order = next((
                item for item in reversed(paper_order_book.history())
                if item.request.strategy_lifecycle_id == lifecycle_id
            ), None)
        except Exception:
            return None
        if order is None:
            return None
        return {
            "order_id": order.order_id,
            "client_order_id": order.request.client_order_id,
        }

    entry_opportunity_value_observer = EntryOpportunityValueRuntimeObserver(
        enabled=(
            operational_configuration.entry_opportunity_value_enabled
            and operational_configuration.warrior_forward_paper_enabled
        ),
        environment=operational_configuration.environment.value,
        path=operational_configuration.entry_opportunity_value_path,
        capacity=operational_configuration.entry_opportunity_value_queue_capacity,
        clock=utc_now,
        research_context_source=(
            trade_intelligence_observer.entry_opportunity_context
        ),
        order_correlation_source=eov_order_correlation,
    )

    warrior_forward_sidecar = WarriorDesktopSidecar(
        enabled=operational_configuration.warrior_forward_paper_enabled,
        storage_path=operational_configuration.warrior_forward_capture_path,
        environment=operational_configuration.environment.value,
        account_context_source=warrior_account_context,
        paper_entry_submitter=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.submit_entry_decision),
        paper_exit_submitter=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.ensure_exit),
        paper_position_quantity_source=(None if paper_trading_commands is None else position_quantity),
        paper_execution_ownership_source=(
            None if autonomous_paper_bridge is None
            else autonomous_paper_bridge.has_execution_ownership
        ),
        execution_quote_source=execution_quote_source,
        research_observer=trade_intelligence_observer,
        entry_value_observer=entry_opportunity_value_observer,
    )

    adaptive_entry_research_observer = AdaptiveWorkingEntryObserver(
        enabled=(
            operational_configuration.adaptive_entry_research_enabled
            and operational_configuration.warrior_forward_paper_enabled
        ),
        environment=operational_configuration.environment.value,
        path=operational_configuration.adaptive_entry_research_path,
        capacity=operational_configuration.adaptive_entry_research_queue_capacity,
        order_source=(
            (lambda _symbol: ()) if paper_order_book is None
            else paper_order_book.open_orders_for_symbol
        ),
        position_source=adaptive_position,
        warrior_source=warrior_forward_sidecar.adaptive_entry_context,
    )

    crypto_data_client = LazyOfficialDataClient(
        lambda: AuditedMarketDataClient(
            MarketDataClientFactory(chart_market_configuration).create(
                timeout_seconds=5.0
            ),
            chart_request_guard,
            chart_market_configuration,
        )
    )
    crypto_research_runtime = CryptoResearchRuntime(
        enabled=operational_configuration.crypto_discovery_enabled,
        provider=WebullCryptoResearchProvider(crypto_data_client),
        configured_pairs=tuple(
            CryptoPair.configured(value)
            for value in operational_configuration.crypto_discovery_symbols
        ),
        path=operational_configuration.crypto_discovery_path,
        queue_capacity=operational_configuration.crypto_discovery_queue_capacity,
        refresh_seconds=operational_configuration.crypto_discovery_refresh_seconds,
        intelligence_history_max_symbols=operational_configuration.crypto_intelligence_history_max_symbols,
        intelligence_history_bar_count=operational_configuration.crypto_intelligence_history_bar_count,
        intelligence_history_request_budget=operational_configuration.crypto_intelligence_history_request_budget,
        intelligence_m1_refresh_seconds=operational_configuration.crypto_intelligence_m1_refresh_seconds,
        intelligence_m5_refresh_seconds=operational_configuration.crypto_intelligence_m5_refresh_seconds,
    )
    crypto_catalyst_acquisition_runtime = None
    if operational_configuration.crypto_catalyst_acquisition_enabled:
        provider_flags = {
            "SEC_EDGAR": operational_configuration.crypto_catalyst_sec_enabled,
            "FEDERAL_REGISTER": operational_configuration.crypto_catalyst_federal_register_enabled,
            "STATUSPAGE": operational_configuration.crypto_catalyst_statuspage_enabled,
            "BYBIT": operational_configuration.crypto_catalyst_bybit_enabled,
        }
        composed_catalyst_providers = tuple(
            adapt_catalyst_provider(
                set_catalyst_provider_enabled(
                    provider,
                    provider_flags.get(str(getattr(provider, "provider_id", "")), False),
                )
            )
            for provider in catalyst_providers
        )
        crypto_catalyst_acquisition_runtime = CryptoCatalystAcquisitionRuntime(
            enabled=True,
            providers=composed_catalyst_providers,
            cadences={
                "SEC_EDGAR": operational_configuration.crypto_catalyst_sec_cadence_seconds,
                "FEDERAL_REGISTER": operational_configuration.crypto_catalyst_federal_register_cadence_seconds,
                "STATUSPAGE": operational_configuration.crypto_catalyst_statuspage_cadence_seconds,
                "BYBIT": operational_configuration.crypto_catalyst_bybit_cadence_seconds,
            },
            provider_enabled=provider_flags,
            scheduler_tick_seconds=operational_configuration.crypto_catalyst_scheduler_tick_seconds,
        )
    crypto_intelligence_runtime = None
    if operational_configuration.crypto_intelligence_enabled:
        collection_config = CryptoCatalystCollectionConfig.from_environment()
        collection_sink = (
            CryptoCatalystCollectionSink(collection_config)
            if collection_config.enabled
            else None
        )
        crypto_intelligence_runtime = CryptoIntelligenceResearchRuntime(
            enabled=True,
            catalyst_view=(None if crypto_catalyst_acquisition_runtime is None else crypto_catalyst_acquisition_runtime),
            collection=collection_sink,
            maximum_active_decisions=operational_configuration.crypto_intelligence_max_active_decisions,
        )
        crypto_research_runtime.configure_intelligence(
            context_sink=crypto_intelligence_runtime.submit_context,
            outcome_sink=crypto_intelligence_runtime.update_outcomes,
        )

    def optional_metrics(root: object | None, *attributes: str) -> dict[str, int]:
        """Resolve a live diagnostic owner without creating or retaining one."""

        current = root
        for attribute in attributes:
            current = getattr(current, attribute, None)
            if current is None:
                return {}
        provider = getattr(current, "memory_metrics", None)
        return {} if not callable(provider) else dict(provider())

    runtime_service_holder: dict[str, object] = {}

    def runtime_driver_metrics(*attributes: str) -> dict[str, int]:
        service = runtime_service_holder.get("service")
        lock = getattr(service, "_lock", None)
        if lock is None:
            return {}
        with lock:
            driver = getattr(service, "_driver", None)
        return optional_metrics(driver, *attributes)

    def realtime_scanner_metrics() -> dict[str, int]:
        return runtime_driver_metrics("_scanner", "_engine")

    memory_observability = MemoryObservability(
        {
            "warrior_forward_runtime": lambda: optional_metrics(
                warrior_forward_sidecar, "_service"
            ),
            "warrior_forward_report": lambda: optional_metrics(
                warrior_forward_sidecar, "_report_worker"
            ),
            "websocket_callback_queue": lambda: runtime_driver_metrics(
                "_market_data"
            ),
            "trade_intelligence_runtime": trade_intelligence_observer.memory_metrics,
            "trade_intelligence_discovery_worker": lambda: optional_metrics(
                trade_intelligence_observer, "_service", "_discovery_worker"
            ),
            "multi_strategy_discovery_engine": lambda: optional_metrics(
                trade_intelligence_observer,
                "_service",
                "_discovery_worker",
                "engine",
            ),
            "realtime_scanner": realtime_scanner_metrics,
            "scanner_snapshot_publisher": lambda: runtime_driver_metrics(
                "_scanner_publisher"
            ),
            "adaptive_entry_runtime": adaptive_entry_research_observer.memory_metrics,
            "adaptive_entry_worker": lambda: optional_metrics(
                adaptive_entry_research_observer, "_worker"
            ),
            "crypto_research": crypto_research_runtime.memory_metrics,
            "paper_order_book": paper_order_book.memory_metrics,
            "order_projection": runtime_projections.order_projection.memory_metrics,
            "position_projection": (
                runtime_projections.position_projection.memory_metrics
            ),
            "decision_projection": (
                runtime_projections.decision_projection.memory_metrics
            ),
            "health_projection": runtime_projections.health_projection.memory_metrics,
            "watchlist_projection": (
                runtime_projections.watchlist_projection.memory_metrics
            ),
            "timeline_projection": runtime_projections.timeline_projection.memory_metrics,
            "application_state": state_store.memory_metrics,
            "operations_bus": bus.memory_metrics,
            "startup": performance_diagnostics.startup_metrics,
        },
        enabled=operational_configuration.memory_observability_enabled,
        path=operational_configuration.memory_observability_path,
        interval_seconds=(
            operational_configuration.memory_observability_interval_seconds
        ),
        tracemalloc_enabled=(
            operational_configuration.memory_tracemalloc_enabled
        ),
        tracemalloc_snapshot_interval_seconds=(
            operational_configuration
            .memory_tracemalloc_snapshot_interval_seconds
        ),
        gc_tracked_objects_enabled=(
            operational_configuration.memory_gc_tracked_objects_enabled
        ),
    )
    if warrior_forward_sidecar.enabled or trade_intelligence_observer.enabled:
        market_event_observer = CompositeMarketEventObserver(
            market_event_observer, warrior_forward_sidecar,
            trade_intelligence_observer, adaptive_entry_research_observer,
        )

    if crypto_research_runtime.enabled:
        crypto_research_runtime.start()
    if crypto_catalyst_acquisition_runtime is not None:
        crypto_catalyst_acquisition_runtime.start()
    if crypto_intelligence_runtime is not None and crypto_intelligence_runtime.enabled:
        crypto_intelligence_runtime.start()

    runtime_service = create_desktop_runtime_service(
        bus,
        driver_factory=driver_factory,
        runtime_mode=configuration.runtime_mode,
        event_sink=runtime_projections.sink,
        market_event_observer=market_event_observer,
    )
    runtime_service_holder["service"] = runtime_service

    trading_service = TradingService(
        placement_runtime,
        cancellation_runtime,
    )
    if memory_observability.enabled:
        memory_observability.record_lifecycle("startup")
        memory_observability.start()

    return DesktopComposition(
        bus=bus,
        state_store=state_store,
        runtime_service=runtime_service,
        trading_service=trading_service,
        order_command_factory=order_command_factory,
        paper_order_book=paper_order_book,
        paper_execution_engine=(
            None
            if paper_trading_commands is None
            else paper_trading_commands.execution_engine
        ),
        paper_trading_commands=paper_trading_commands,
        runtime_projections=runtime_projections,
        chart_market_data_service=chart_market_data_service,
        chart_default_symbol=chart_default_symbol,
        warrior_forward_sidecar=warrior_forward_sidecar,
        autonomous_paper_bridge=autonomous_paper_bridge,
        trade_intelligence_observer=trade_intelligence_observer,
        entry_opportunity_value_observer=entry_opportunity_value_observer,
        adaptive_entry_research_observer=adaptive_entry_research_observer,
        crypto_research_runtime=crypto_research_runtime,
        crypto_intelligence_runtime=crypto_intelligence_runtime,
        crypto_catalyst_acquisition_runtime=crypto_catalyst_acquisition_runtime,
        memory_observability=memory_observability,
    )
__all__ = [
    "DesktopComposition",
    "create_desktop_composition",
]

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

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
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.forward_models import PaperAccountContext
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.execution_quote import WebullExecutionQuoteSource
from app.strategies.warrior_momentum.forward_runtime import management_context_available
from app.strategies.warrior_momentum.observability import create_warrior_observability_sink
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver
from app.trade_intelligence.taxonomy_paper_bridge import TaxonomyPaperExecutionBridge
from app.trade_intelligence.decision_intelligence import HistoricalDecisionIntelligence
from app.trade_intelligence.decision_intelligence.entry_timing import (
    EntryIntelligenceConfig, HistoricalPaperEntryTimingPolicy, PAPER_ONLY,
)
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
from app.paper_gateway.durable_store import NO_ACTIVE_PAPER_CAMPAIGN_ID
from app.portfolio_intelligence import PortfolioAccount, PortfolioIntelligenceService, PortfolioRiskLimits, load_portfolio_intelligence_configuration
from app.symbol_intelligence.composition import (
    SymbolIntelligenceComposition,
    SymbolIntelligenceRepositoryComposition,
    create_symbol_intelligence_composition,
)
from app.symbol_intelligence.network_ownership_runtime import evaluate_sec_acquisition_eligibility
from app.composition.sec_shadow_runtime import (
    SecShadowRuntimeComposition,
    SecShadowRuntimeState,
    create_sec_shadow_runtime,
)


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
    paper_entry_intelligence: HistoricalPaperEntryTimingPolicy | None = None
    trade_intelligence_observer: TradeIntelligenceRuntimeObserver | None = None
    entry_opportunity_value_observer: EntryOpportunityValueRuntimeObserver | None = None
    adaptive_entry_research_observer: AdaptiveWorkingEntryObserver | None = None
    crypto_research_runtime: CryptoResearchRuntime | None = None
    crypto_intelligence_runtime: CryptoIntelligenceResearchRuntime | None = None
    crypto_catalyst_acquisition_runtime: CryptoCatalystAcquisitionRuntime | None = None
    memory_observability: MemoryObservability | None = None
    symbol_intelligence: SymbolIntelligenceComposition | None = None
    sec_shadow_runtime: SecShadowRuntimeComposition | None = None
    symbol_intelligence_activation_state: str = "DISABLED"

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        """Close composed resources in lifecycle order."""

        symbol_intelligence_stopped = True

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
        if self.symbol_intelligence is not None:
            try:
                symbol_intelligence_stopped = self.symbol_intelligence.close(
                    timeout_seconds=min(timeout_seconds, 5.0)
                )
            except Exception:
                symbol_intelligence_stopped = False
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
        try:
            performance_diagnostics.finish_run()
        except Exception:
            # Performance evidence is strictly non-authoritative.
            pass
        return runtime_stopped and symbol_intelligence_stopped


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
    shadow_runtime_factory: Callable[..., SecShadowRuntimeComposition] = create_sec_shadow_runtime,
) -> DesktopComposition:
    """Construct the desktop application dependency graph."""

    performance_diagnostics.start_run()
    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)

    if (placement_runtime is None) != (cancellation_runtime is None):
        raise ValueError(
            "placement_runtime and cancellation_runtime must be provided together"
        )

    operational_configuration = load_configuration()
    warrior_observability = create_warrior_observability_sink(SimpleNamespace(
        observability_enabled=os.getenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ENABLED", "false").strip().lower() == "true",
        observability_root=os.getenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ROOT") or None,
        observability_session_id=os.getenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_SESSION_ID") or None,
    ))
    configured_warrior_strategy = WarriorMomentumConfig.from_env()
    warrior_strategy_config = WarriorMomentumConfig(
        adaptive_context_enabled=(
            configured_warrior_strategy.adaptive_context_enabled
            and operational_configuration.environment.value == "PAPER"
        )
    )
    trade_intelligence_observer = TradeIntelligenceRuntimeObserver(
        enabled=(
            operational_configuration.trade_intelligence_enabled
            and not operational_configuration.live_trading_enabled
        ),
        environment=operational_configuration.environment.value,
        path=operational_configuration.trade_intelligence_path,
        capacity=operational_configuration.trade_intelligence_queue_capacity,
        observability=warrior_observability,
        warrior_observation_enabled=warrior_strategy_config.adaptive_context_enabled,
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
    paper_campaign_holder: dict[str, object] = {"id": None, "capital": None}
    def portfolio_account_source() -> PortfolioAccount:
        state = state_store.snapshot()
        account = state.broker_account
        if operational_configuration.environment.value == "PAPER":
            paper_account = state.paper_account
            if paper_account is not None:
                return PortfolioAccount(
                    paper_account.campaign_id, paper_account.current_equity,
                    paper_account.current_cash, paper_account.buying_power,
                )
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
        paper_account_campaign_id_source=lambda: paper_campaign_holder["id"],
        paper_account_capital_source=lambda: paper_campaign_holder["capital"],
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
    decision_intelligence_observer = None
    paper_environment = operational_configuration.environment.value == "PAPER"
    paper_entry_intelligence = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(
            enabled=(
                paper_environment
                and operational_configuration.historical_entry_experiment_enabled
            ),
            mode=operational_configuration.historical_entry_experiment_mode,
            environment=PAPER_ONLY,
            trading_environment=operational_configuration.environment.value,
            live_trading_enabled=operational_configuration.live_trading_enabled,
            warrior_forward_paper_enabled=operational_configuration.warrior_forward_paper_enabled,
            paper_symbol_authorization_mode=operational_configuration.paper_symbol_authorization_mode.value,
            journal_path=(
                str(operational_configuration.historical_entry_experiment_path)
                if paper_environment else None
            ),
        )
    )
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
            if decision_intelligence_observer is not None:
                decision_intelligence_observer.observe_paper_event(event)
            paper_entry_intelligence.observe_paper_event(event)
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
        paper_campaign_holder["id"] = paper_trading_commands.paper_campaign_id
        paper_campaign_holder["capital"] = (
            paper_trading_commands.durable_store.active_campaign_capital()
            if paper_trading_commands.durable_store is not None else None
        )
        runtime_projections.paper_account_projection.refresh()
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
        paper_account = (
            state.paper_account
            if operational_configuration.environment.value == "PAPER"
            else None
        )
        if paper_account is not None:
            equity = paper_account.current_equity
            buying_power = paper_account.buying_power
        elif account is not None:
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
            durable_store=paper_trading_commands.durable_store,
            position_quantity_source=position_quantity,
            management_context_source=lambda symbol: management_context_available(
                operational_configuration.warrior_forward_capture_path, symbol,
                configuration_fingerprint=strategy_configuration_fingerprint(),
                allow_compatible_generation=True,
            ),
        )
        # Restore the read model from the authoritative PAPER order book before
        # any management/protection callbacks can observe a live quote.  The
        # bridge is deliberately held in reconciliation until this seed is
        # complete.
        runtime_projections.position_projection.reconcile_from_paper_orders(
            paper_trading_commands.order_book.history(),
        )
        runtime_projections.paper_account_projection.refresh()
        autonomous_paper_bridge.begin_reconciliation()
        autonomous_paper_bridge.reconcile()
        autonomous_paper_bridge.reconcile_protection()

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

    taxonomy_execution_bridge = None
    if operational_configuration.environment.value == "PAPER":
        decision_intelligence_observer = HistoricalDecisionIntelligence()
        taxonomy_execution_bridge = TaxonomyPaperExecutionBridge(
            recovery=decision_intelligence_observer.taxonomy_recovery
        )
    warrior_forward_sidecar = WarriorDesktopSidecar(
        enabled=operational_configuration.warrior_forward_paper_enabled,
        storage_path=operational_configuration.warrior_forward_capture_path,
        environment=operational_configuration.environment.value,
        strategy_config=warrior_strategy_config,
        account_context_source=warrior_account_context,
        paper_entry_submitter=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.submit_entry_decision),
        paper_entry_replacer=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.consider_entry_replacement),
        paper_entry_rearmer=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.submit_rearmed_entry),
        paper_exit_submitter=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.ensure_exit),
        paper_position_quantity_source=(None if paper_trading_commands is None else position_quantity),
        paper_execution_ownership_source=(
            None if autonomous_paper_bridge is None
            else autonomous_paper_bridge.has_execution_ownership
        ),
        paper_working_entry_source=(
            None if autonomous_paper_bridge is None
            else autonomous_paper_bridge.has_working_entry
        ),
        execution_quote_source=execution_quote_source,
        order_flow_client=shared_rest_market_data,
        research_observer=trade_intelligence_observer,
        entry_value_observer=entry_opportunity_value_observer,
        paper_campaign_id=(
            None if paper_trading_commands is None
            else paper_trading_commands.paper_campaign_id
            or NO_ACTIVE_PAPER_CAMPAIGN_ID
        ),
        taxonomy_execution_bridge=taxonomy_execution_bridge,
        decision_intelligence_observer=decision_intelligence_observer,
        paper_entry_intelligence=paper_entry_intelligence,
        observability=warrior_observability,
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
            async_projections=True,
        )

    if crypto_research_runtime.enabled:
        crypto_research_runtime.start()
    if crypto_catalyst_acquisition_runtime is not None:
        crypto_catalyst_acquisition_runtime.start()
    if crypto_intelligence_runtime is not None and crypto_intelligence_runtime.enabled:
        crypto_intelligence_runtime.start()

    # D2B1 consumes the existing D1 predicate.  Repository opening is local;
    # all network-capable construction remains behind lease authorization.
    symbol_intelligence = None
    symbol_intelligence_activation_state = "DISABLED"
    activation_state_holder = [symbol_intelligence_activation_state]
    try:
        eligible = evaluate_sec_acquisition_eligibility(operational_configuration).eligible
        if not eligible:
            symbol_intelligence_activation_state = "INELIGIBLE"
        if eligible:
            symbol_intelligence = create_symbol_intelligence_composition(
                operational_configuration, activate=True, start=False,
                activation_diagnostics_callback=lambda state: activation_state_holder.__setitem__(0, state),
            )
            symbol_intelligence_activation_state = activation_state_holder[0]
            symbol_intelligence_activation_state = (
                "ACTIVE" if symbol_intelligence is not None else "LEASE_DENIED"
            )
    except Exception:
        # Migration acquisition is isolated from the legacy desktop path.
        symbol_intelligence = None
        symbol_intelligence_activation_state = "CONSTRUCTION_ERROR"

    shared_repository_composition = (
        None if symbol_intelligence is None
        else SymbolIntelligenceRepositoryComposition(symbol_intelligence.repository)
    )
    shadow_construction_failed = False
    try:
        if shared_repository_composition is not None:
            parameters = inspect.signature(shadow_runtime_factory).parameters
            accepts_repository = (
                "repository_composition" in parameters
                or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
            )
            if accepts_repository:
                sec_shadow_runtime = shadow_runtime_factory(
                    operational_configuration,
                    repository_composition=shared_repository_composition,
                )
            else:
                # Preserve narrowly-scoped test/extension factories using the
                # pre-D2B single-argument contract.
                sec_shadow_runtime = shadow_runtime_factory(operational_configuration)
        else:
            sec_shadow_runtime = shadow_runtime_factory(operational_configuration)
    except Exception:
        # Shadow is observational only.  Its failure must not terminate the
        # legacy desktop path, but acquisition ownership must remain reachable
        # if cleanup cannot be confirmed complete.
        shadow_construction_failed = True
        symbol_intelligence_activation_state = "CONSTRUCTION_ERROR"
        sec_shadow_runtime = SecShadowRuntimeComposition(
            state=SecShadowRuntimeState.CONSTRUCTION_ERROR,
        )
        if symbol_intelligence is not None:
            if symbol_intelligence.close(timeout_seconds=5.0):
                symbol_intelligence = None

    try:
        runtime_service = create_desktop_runtime_service(
            bus,
            driver_factory=driver_factory,
            runtime_mode=configuration.runtime_mode,
            event_sink=runtime_projections.sink,
            market_event_observer=market_event_observer,
            sec_shadow_evaluator=sec_shadow_runtime.evaluator,
        )
        runtime_service_holder["service"] = runtime_service
        trading_service = TradingService(
            placement_runtime,
            cancellation_runtime,
        )
    except Exception as error:
        # No DesktopComposition exists yet, so propagate the original failure
        # with the owner attached when cleanup is incomplete.  This keeps a
        # live worker/heartbeat/lease reachable for deterministic retry.
        if symbol_intelligence is not None:
            try:
                cleanup_complete = symbol_intelligence.close(timeout_seconds=5.0)
            except Exception:
                cleanup_complete = False
            if not cleanup_complete:
                setattr(error, "symbol_intelligence_holder", symbol_intelligence)
        raise
    if memory_observability.enabled:
        memory_observability.record_lifecycle("startup")
        memory_observability.start()

    result = DesktopComposition(
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
        paper_entry_intelligence=paper_entry_intelligence,
        trade_intelligence_observer=trade_intelligence_observer,
        entry_opportunity_value_observer=entry_opportunity_value_observer,
        adaptive_entry_research_observer=adaptive_entry_research_observer,
        crypto_research_runtime=crypto_research_runtime,
        crypto_intelligence_runtime=crypto_intelligence_runtime,
        crypto_catalyst_acquisition_runtime=crypto_catalyst_acquisition_runtime,
        memory_observability=memory_observability,
        symbol_intelligence=symbol_intelligence,
        sec_shadow_runtime=sec_shadow_runtime,
    )
    if symbol_intelligence is not None and not shadow_construction_failed:
        try:
            if not symbol_intelligence.start():
                if symbol_intelligence.close():
                    result.symbol_intelligence = None
                result.symbol_intelligence_activation_state = "START_ERROR"
        except Exception:
            try:
                cleanup_complete = symbol_intelligence.close()
            except Exception:
                cleanup_complete = False
            if cleanup_complete:
                result.symbol_intelligence = None
            result.symbol_intelligence_activation_state = "START_ERROR"
    result.symbol_intelligence_activation_state = (
        "ACTIVE" if result.symbol_intelligence is not None
        else symbol_intelligence_activation_state
    )
    return result
__all__ = [
    "DesktopComposition",
    "create_desktop_composition",
]

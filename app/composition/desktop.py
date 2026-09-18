from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
from types import SimpleNamespace

from app.configuration import load_configuration
from app.operations_core import ApplicationStateStore, OperationsBus
from app.order_cancellation import OrderCancellationRuntime
from app.order_placement import OrderPlacementRuntime
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.execution_engine import PaperExecutionEngine
from app.services import OrderCommandFactory, RuntimeService, TradingService
from app.operations.runtime import PaperRuntimeEvent
from app.webull.client_factories import (
    market_data_configuration,
    trading_configuration,
)
from app.webull.request_audit import RequestIsolationGuard
from app.webull.market_data_session import utc_now
from app.strategies.warrior_momentum.desktop_sidecar import (
    CompositeMarketEventObserver, WarriorDesktopSidecar,
    strategy_configuration_fingerprint,
)
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.forward_runtime import management_context_available
from app.strategies.warrior_momentum.observability import create_warrior_observability_sink
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver
from app.trade_intelligence.taxonomy_paper_bridge import TaxonomyPaperExecutionBridge
from app.trade_intelligence.decision_intelligence import HistoricalDecisionIntelligence
from app.trade_intelligence.decision_intelligence.entry_timing import (
    EntryIntelligenceConfig, HistoricalPaperEntryTimingPolicy, PAPER_ONLY,
)
from app.memory_observability import MemoryObservability
from app.performance_diagnostics import performance_diagnostics
from app.crypto_research import (
    CryptoCatalystAcquisitionRuntime,
    CryptoIntelligenceResearchRuntime,
    CryptoResearchRuntime,
)
from .desktop_optional_research import create_optional_research_runtimes
from .desktop_observability import RuntimeDriverMetricsSource, create_desktop_memory_observability
from .desktop_market_services import create_desktop_market_services
from .desktop_trading_state import DesktopTradingStateSources
from .desktop_research_observers import (
    create_adaptive_entry_research_observer,
    create_entry_opportunity_value_observer,
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
from app.portfolio_intelligence import PortfolioIntelligenceService, PortfolioRiskLimits, load_portfolio_intelligence_configuration
from app.symbol_intelligence.composition import SymbolIntelligenceComposition, create_symbol_intelligence_composition
from app.composition.sec_shadow_runtime import (
    SecShadowRuntimeComposition,
    create_sec_shadow_runtime,
)
from .desktop_symbol_intelligence import (
    create_desktop_symbol_intelligence_bundle,
    start_desktop_symbol_intelligence,
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


    trading_state_holder: dict[str, DesktopTradingStateSources] = {}

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
        portfolio_account_source=lambda: trading_state_holder["sources"].portfolio_account(),
        portfolio_intelligence_service=PortfolioIntelligenceService(
            configuration=load_portfolio_intelligence_configuration(),
            limits=PortfolioRiskLimits(
                maximum_open_positions=operational_configuration.max_open_positions,
            )
        ),
        paper_account_campaign_id_source=lambda: paper_campaign_holder["id"],
        paper_account_capital_source=lambda: paper_campaign_holder["capital"],
    )
    trading_state_sources = DesktopTradingStateSources(
        state_store=state_store,
        runtime_projections=runtime_projections,
        operational_configuration=operational_configuration,
    )
    trading_state_holder["sources"] = trading_state_sources
    trade_intelligence_observer.bind_authoritative_focus_sources(
        position_source=lambda: runtime_projections.position_projection.snapshot,
        order_source=lambda: runtime_projections.order_projection.snapshot,
    )
    market_services = create_desktop_market_services(
        chart_market_configuration=chart_market_configuration,
        chart_request_guard=chart_request_guard,
        runtime_projections=runtime_projections,
    )
    shared_rest_market_data = market_services.shared_rest_market_data
    chart_market_data_service = market_services.chart_market_data_service
    execution_quote_source = market_services.execution_quote_source


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
            position_average_cost_source=trading_state_sources.position_average_cost,
            position_quantity_source=trading_state_sources.position_quantity,
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


    autonomous_paper_bridge = None
    if paper_trading_commands is not None:
        autonomous_paper_bridge = AutonomousPaperExecutionBridge(
            paper_trading_commands.trading_service,
            paper_trading_commands.order_command_factory,
            mode=configuration.runtime_mode.value,
            enabled=operational_configuration.warrior_forward_paper_enabled,
            order_book=paper_trading_commands.order_book,
            durable_store=paper_trading_commands.durable_store,
            position_quantity_source=trading_state_sources.position_quantity,
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

    entry_opportunity_value_observer = create_entry_opportunity_value_observer(
        operational_configuration=operational_configuration,
        trade_intelligence_observer=trade_intelligence_observer,
        paper_order_book=paper_order_book,
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
        account_context_source=trading_state_sources.warrior_account_context,
        paper_entry_submitter=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.submit_entry_decision),
        paper_entry_replacer=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.consider_entry_replacement),
        paper_entry_rearmer=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.submit_rearmed_entry),
        paper_exit_submitter=(None if autonomous_paper_bridge is None else autonomous_paper_bridge.ensure_exit),
        paper_position_quantity_source=(None if paper_trading_commands is None else trading_state_sources.position_quantity),
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

    adaptive_entry_research_observer = create_adaptive_entry_research_observer(
        operational_configuration=operational_configuration,
        paper_order_book=paper_order_book,
        position_source=trading_state_sources.adaptive_position,
        warrior_forward_sidecar=warrior_forward_sidecar,
    )

    optional_research = create_optional_research_runtimes(
        operational_configuration=operational_configuration,
        chart_market_configuration=chart_market_configuration,
        chart_request_guard=chart_request_guard,
        catalyst_providers=catalyst_providers,
    )
    crypto_research_runtime = optional_research.crypto_research_runtime
    crypto_catalyst_acquisition_runtime = (
        optional_research.crypto_catalyst_acquisition_runtime
    )
    crypto_intelligence_runtime = optional_research.crypto_intelligence_runtime

    runtime_driver_metrics = RuntimeDriverMetricsSource()

    memory_observability = create_desktop_memory_observability(
        operational_configuration=operational_configuration,
        warrior_forward_sidecar=warrior_forward_sidecar,
        runtime_driver_metrics=runtime_driver_metrics,
        trade_intelligence_observer=trade_intelligence_observer,
        adaptive_entry_research_observer=adaptive_entry_research_observer,
        crypto_research_runtime=crypto_research_runtime,
        paper_order_book=paper_order_book,
        runtime_projections=runtime_projections,
        state_store=state_store,
        bus=bus,
        observability_factory=MemoryObservability,
    )

    if warrior_forward_sidecar.enabled or trade_intelligence_observer.enabled:
        market_event_observer = CompositeMarketEventObserver(
            market_event_observer, warrior_forward_sidecar,
            trade_intelligence_observer, adaptive_entry_research_observer,
            async_projections=True,
        )

    optional_research.start()

    # D2B1 consumes the existing D1 predicate.  Repository opening is local;
    # all network-capable construction remains behind lease authorization.
    symbol_intelligence_bundle = create_desktop_symbol_intelligence_bundle(
        operational_configuration=operational_configuration,
        shadow_runtime_factory=shadow_runtime_factory,
        symbol_intelligence_factory=create_symbol_intelligence_composition,
    )
    symbol_intelligence = symbol_intelligence_bundle.symbol_intelligence
    symbol_intelligence_activation_state = (
        symbol_intelligence_bundle.activation_state
    )
    sec_shadow_runtime = symbol_intelligence_bundle.sec_shadow_runtime

    try:
        runtime_service = create_desktop_runtime_service(
            bus,
            driver_factory=driver_factory,
            runtime_mode=configuration.runtime_mode,
            event_sink=runtime_projections.sink,
            market_event_observer=market_event_observer,
            sec_shadow_evaluator=sec_shadow_runtime.evaluator,
        )
        runtime_driver_metrics.bind(runtime_service)
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
    result.symbol_intelligence_activation_state = (
        start_desktop_symbol_intelligence(symbol_intelligence_bundle)
    )
    result.symbol_intelligence = symbol_intelligence_bundle.symbol_intelligence
    return result
__all__ = [
    "DesktopComposition",
    "create_desktop_composition",
]

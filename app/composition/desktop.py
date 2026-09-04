from __future__ import annotations

from collections.abc import Callable
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
)
from app.strategies.warrior_momentum.forward_models import PaperAccountContext
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.execution_quote import WebullExecutionQuoteSource
from app.strategies.warrior_momentum.forward_runtime import management_context_available
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver
from app.entry_opportunity_value import EntryOpportunityValueRuntimeObserver

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

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        """Close composed resources in lifecycle order."""

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
        normalized = symbol.strip().upper()
        position = next(
            (
                item
                for item in runtime_projections.position_projection.snapshot.positions
                if item.symbol == normalized
            ),
            None,
        )
        return (
            None
            if position is None
            else Decimal(position.average_cost)
        )

    def position_quantity(symbol: str) -> Decimal:
        normalized = symbol.strip().upper()
        position = next(
            (
                item
                for item in runtime_projections.position_projection.snapshot.positions
                if item.symbol == normalized
            ),
            None,
        )
        return (
            Decimal("0")
            if position is None
            else Decimal(position.quantity)
        )

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
                operational_configuration.warrior_forward_capture_path, symbol
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
    if warrior_forward_sidecar.enabled or trade_intelligence_observer.enabled:
        market_event_observer = CompositeMarketEventObserver(
            market_event_observer, warrior_forward_sidecar,
            trade_intelligence_observer,
        )

    runtime_service = create_desktop_runtime_service(
        bus,
        driver_factory=driver_factory,
        runtime_mode=configuration.runtime_mode,
        event_sink=runtime_projections.sink,
        market_event_observer=market_event_observer,
    )

    trading_service = TradingService(
        placement_runtime,
        cancellation_runtime,
    )

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
    )
__all__ = [
    "DesktopComposition",
    "create_desktop_composition",
]

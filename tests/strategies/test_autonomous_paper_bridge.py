from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.composition.runtime_mode import RuntimeMode
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.services.order_command_factory import OrderEntryCommand
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousManagementReadiness, AutonomousPaperReadiness,
)


@dataclass(frozen=True)
class Signal:
    symbol: str = "PMI"
    entry_trigger: Decimal = Decimal("10")
    lifecycle_id: str = "trade-a"


def test_paper_bridge_submits_exactly_one_entry_and_exit_per_transition() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        mode=RuntimeMode.PAPER.value, order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is True
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    assert len(composition.order_book.open_orders()) == 1
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "TARGET") is True
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "TARGET") is False
    assert len(composition.order_book.open_orders()) == 2
    composition.close()


def test_bridge_refuses_non_paper_mode_without_invoking_order_port() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        mode="LIVE", order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    assert composition.order_book.history() == ()
    composition.close()


def test_paper_bridge_round_trip_uses_gateway_fill_lifecycle() -> None:
    events = []
    composition = create_paper_trading_command_composition(
        event_sink=events.append,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is True
    entry_reports = composition.gateway.process_market_event(MarketEvent(
        1, datetime.now(timezone.utc), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal("9.99"), Decimal("10"), Decimal("100"), Decimal("100")),
    ))
    assert entry_reports and entry_reports[0].fills
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "TARGET") is True
    exit_reports = composition.gateway.process_market_event(MarketEvent(
        2, datetime.now(timezone.utc), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal("10.50"), Decimal("10.51"), Decimal("100"), Decimal("100")),
    ))
    assert exit_reports and exit_reports[0].fills
    assert events[-1].fill is not None
    assert events[-1].fill.realized_pnl == Decimal("50.00")
    composition.close()


def test_same_symbol_sequential_lifecycles_allow_reentry_and_same_exit_reason() -> None:
    quantity = {"PMI": Decimal("100")}
    events = []
    composition = create_paper_trading_command_composition(
        event_sink=events.append,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda symbol: quantity.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )

    def quote(sequence: int, bid: str, ask: str) -> None:
        composition.gateway.process_market_event(MarketEvent(
            sequence, datetime.now(timezone.utc), "PMI", "test", MarketEventType.QUOTE,
            QuotePayload(Decimal(bid), Decimal(ask), Decimal("100"), Decimal("100")),
        ))

    trade_a = Signal(lifecycle_id="trade-a")
    assert bridge.submit_entry(trade_a, 100, Decimal("50")) is True
    assert bridge.submit_entry(trade_a, 100, Decimal("50")) is False
    quote(1, "9.99", "10")
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "STOP", "trade-a") is True
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "STOP", "trade-a") is False
    quote(2, "10.50", "10.51")
    quantity["PMI"] = Decimal("0")

    trade_b = Signal(entry_trigger=Decimal("11"), lifecycle_id="trade-b")
    assert bridge.submit_entry(trade_b, 100, Decimal("50")) is True
    assert bridge.submit_entry(trade_b, 100, Decimal("50")) is False
    quote(3, "10.99", "11")
    quantity["PMI"] = Decimal("100")
    assert bridge.submit_exit("PMI", 100, Decimal("9.75"), "STOP", "trade-b") is True
    assert bridge.submit_exit("PMI", 100, Decimal("9.75"), "STOP", "trade-b") is False
    quote(4, "9.75", "9.76")

    assert len(composition.order_book.history()) == 4
    fills = [event.fill for event in events if event.fill is not None]
    assert len(fills) == 4
    assert sum((fill.realized_pnl for fill in fills), Decimal("0")) == Decimal("25.00")
    assert bridge.submit_entry(trade_a, 100, Decimal("50")) is False
    assert bridge.submit_exit("PMI", 100, Decimal("9.75"), "STOP", "trade-a") is False
    composition.close()


def _paper_quote(composition, sequence: int, bid: str, ask: str) -> None:
    composition.gateway.process_market_event(MarketEvent(
        sequence, datetime.now(timezone.utc), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal(bid), Decimal(ask), Decimal("100"), Decimal("100")),
    ))


def test_restart_working_entry_reconciles_and_later_fills_once(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    first.close()
    second = create_paper_trading_command_composition(persistence_path=str(path))
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book)
    recovered.begin_reconciliation()
    assert recovered.submit_entry(Signal(), 100, Decimal("50")) is False
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    _paper_quote(second, 10, "9.99", "10")
    assert len(second.order_book.history()) == 1
    assert second.order_book.history()[0].filled_quantity == Decimal("100")
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.RECONCILIATION_REQUIRED
    second.close()


def test_restart_open_position_blocks_duplicate_entry_and_uses_restored_quantity(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    first.close()
    second = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book, position_quantity_source=lambda _symbol: Decimal("100"))
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.submit_entry(Signal(), 100, Decimal("50")) is False
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.RECONCILIATION_REQUIRED
    assert recovered.submit_exit("PMI", 100, Decimal("10.50"), "STOP") is False
    second.close()


def test_restart_pending_exit_suppresses_duplicate_and_future_fill_closes(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "STOP")
    first.close()
    second = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book, position_quantity_source=lambda _symbol: Decimal("100"))
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.submit_exit("PMI", 100, Decimal("10.50"), "STOP") is False
    _paper_quote(second, 2, "10.50", "10.51")
    assert len(second.order_book.history()) == 2
    assert second.order_book.open_orders() == ()
    second.close()


def test_recovered_position_management_requires_context(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    first.close()
    second = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book, position_quantity_source=lambda _symbol: Decimal("100"))
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.RECONCILIATION_REQUIRED
    second.close()


def test_recovered_position_with_verified_context_is_management_ready(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    first.close()
    second = create_paper_trading_command_composition(persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"))
    recovered = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory, order_book=second.order_book,
        position_quantity_source=lambda _symbol: Decimal("100"),
        management_context_source=lambda _symbol: "trade-a",
    )
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.READY
    second.close()


def test_reconciliation_barrier_and_contradiction_fail_closed(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    composition = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(composition.trading_service, composition.order_command_factory, order_book=composition.order_book)
    bridge.begin_reconciliation()
    assert bridge.readiness is AutonomousPaperReadiness.RECONCILING
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    assert bridge.reconcile() is AutonomousPaperReadiness.READY
    composition.close()

    first = create_paper_trading_command_composition(persistence_path=str(path))
    command = OrderEntryCommand(symbol="PMI", side="BUY", quantity=Decimal("100"), order_type="LIMIT", limit_price=Decimal("10"), stop_price=None, time_in_force="DAY")
    assert first.trading_service.place_order(first.order_command_factory.create_placement_request(command)).success
    command = OrderEntryCommand(symbol="PMI", side="BUY", quantity=Decimal("100"), order_type="LIMIT", limit_price=Decimal("10"), stop_price=None, time_in_force="DAY")
    assert first.trading_service.place_order(first.order_command_factory.create_placement_request(command)).success
    first.close()
    blocked = create_paper_trading_command_composition(persistence_path=str(path))
    blocked_bridge = AutonomousPaperExecutionBridge(blocked.trading_service, blocked.order_command_factory, order_book=blocked.order_book)
    assert blocked_bridge.reconcile() is AutonomousPaperReadiness.BLOCKED
    assert blocked_bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    blocked.close()

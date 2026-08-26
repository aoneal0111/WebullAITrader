from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.composition.runtime_mode import RuntimeMode
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge


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

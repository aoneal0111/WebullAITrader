from decimal import Decimal

from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.operations_core import OperationsBus
from tests.test_support.session_clock import (
    create_session_paper_composition as create_paper_trading_command_composition,
    session_timestamp,
)
from app.services.order_command_factory import OrderEntryCommand
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload


def _quote(composition, sequence, bid, ask):
    return composition.gateway.process_market_event(MarketEvent(
        sequence, session_timestamp(sequence), "PMI", "integration", MarketEventType.QUOTE,
        QuotePayload(Decimal(bid), Decimal(ask), Decimal("100"), Decimal("100")),
    ))


def _qty(pipeline):
    return Decimal(pipeline.position_projection.snapshot.positions[0].quantity)


def test_restart_exit_fill_clears_restored_position_and_pnl(tmp_path):
    path = tmp_path / "paper.sqlite3"
    bus_a = OperationsBus()
    pipeline_a = create_runtime_projection_pipeline(operations_bus=bus_a, account_id="paper-account")
    first = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=pipeline_a.sink,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    buy = first.order_command_factory.create_placement_request(OrderEntryCommand(
        symbol="PMI", side="BUY", quantity=Decimal("100"), order_type="LIMIT",
        limit_price=Decimal("10"), stop_price=None, time_in_force="DAY",
        strategy_lifecycle_id="lifecycle-x",
    ))
    assert first.trading_service.place_order(buy).success
    _quote(first, 1, "9.99", "10")
    assert _qty(pipeline_a) == Decimal("100")
    sell = first.order_command_factory.create_placement_request(OrderEntryCommand(
        symbol="PMI", side="SELL", quantity=Decimal("100"), order_type="LIMIT",
        limit_price=Decimal("10.25"), stop_price=None, time_in_force="DAY",
        strategy_lifecycle_id="lifecycle-x",
    ))
    assert first.trading_service.place_order(sell).success
    first.close()

    bus_b = OperationsBus()
    pipeline_b = create_runtime_projection_pipeline(operations_bus=bus_b, account_id="paper-account")
    second = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=pipeline_b.sink,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    assert _qty(pipeline_b) == Decimal("100")
    restored = second.order_book.history()
    assert len(restored) == 2
    assert all(order.request.strategy_lifecycle_id == "lifecycle-x" for order in restored)
    reports = _quote(second, 2, "10.25", "10.26")
    assert reports and reports[0].fills
    assert reports[0].fills[0].quantity == Decimal("100")
    assert _qty(pipeline_b) == Decimal("0")
    assert Decimal(pipeline_b.position_projection.snapshot.positions[0].realized_gain_loss) == Decimal("25.00")
    assert len(second.order_book.history()) == 2
    assert sum(len(order.fills) for order in second.order_book.history()) == 2
    second.close()

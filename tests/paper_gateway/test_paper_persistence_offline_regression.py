"""Synthetic, isolated PAPER persistence/stop rehydration regression.

Uses a temporary SQLite file; never reads a workstation ledger or contacts Webull.
"""
from decimal import Decimal

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.strategy_research.paper_ledger_provenance import inspect_ledger
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from tests.test_support.session_clock import (
    create_session_paper_composition,
    session_timestamp,
)


class _Signal:
    symbol = "PMI"
    entry_trigger = Decimal("10")
    lifecycle_id = "isolated-stop-restart"


def test_isolated_paper_ledger_reopens_with_working_stop_and_does_not_duplicate(tmp_path):
    path = tmp_path / "paper-execution.sqlite3"
    assert not path.exists()
    first = create_session_paper_composition(
        persistence_path=str(path),
        position_average_cost_source=lambda _: Decimal("10"),
        position_quantity_source=lambda _: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service, first.order_command_factory, order_book=first.order_book,
    )
    assert bridge.submit_entry(_Signal(), 100, Decimal("50"))
    quote = MarketEvent(
        1, session_timestamp(1), "PMI", "synthetic", MarketEventType.QUOTE,
        QuotePayload(Decimal("9.99"), Decimal("10"), Decimal("100"), Decimal("100")),
    )
    first.gateway.process_market_event(quote)
    assert bridge.submit_exit("PMI", 100, Decimal("9.50"), "STOP")
    before = tuple((o.order_id, o.status.value) for o in first.order_book.history())
    assert len(before) == 2
    assert len(first.order_book.open_orders()) == 1
    first.close()

    schema = inspect_ledger(path)
    assert schema["classification"] == "PAPER_SCHEMA_WITH_ORDERS"
    assert schema["order_count"] == 2
    assert schema["authoritative_runtime_proven"] is False

    second = create_session_paper_composition(
        persistence_path=str(path),
        position_average_cost_source=lambda _: Decimal("10"),
        position_quantity_source=lambda _: Decimal("100"),
    )
    after = tuple((o.order_id, o.status.value) for o in second.order_book.history())
    # Rehydration may enumerate rows in a different order; identity and state must agree.
    assert len(after) == len(before)
    assert dict(after) == dict(before)
    assert len({order_id for order_id, _ in after}) == len(after)
    assert len(second.order_book.open_orders()) == 1
    restored = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory, order_book=second.order_book,
    )
    assert restored.submit_exit("PMI", 100, Decimal("9.50"), "STOP") is False
    assert len(second.order_book.history()) == 2
    second.close()
    assert inspect_ledger(path)["order_count"] == 2

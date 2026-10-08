"""Exercise synchronous desktop projection -> scalper -> gateway feedback."""
from decimal import Decimal as D

from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.operations_core import OperationsBus
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.paper_trading.order_models import OrderSide, OrderStatus
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig
from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
from tests.test_support.session_clock import create_session_paper_composition
from tests.warrior_momentum.test_quick_scalper_runtime import NOW, account, quote, snapshot, Quotes, paper_order
import pytest


def test_partial_entry_and_target_close_through_synchronous_feedback():
    pipeline = create_runtime_projection_pipeline(
        operations_bus=OperationsBus(), account_id="feedback",
        paper_account_starting_cash=D("100000"),
    )
    holder = {}
    delivered = []

    def quantity(symbol):
        return sum((D(p.quantity) for p in pipeline.position_projection.snapshot.positions
                    if p.symbol == symbol), D("0"))

    def sink(event):
        delivered.append(event)
        # Fail fast rather than allowing the old recursion to exhaust the stack.
        assert len(delivered) < 40
        pipeline.sink(event)
        if "runtime" in holder:
            holder["runtime"].observe_paper_event(event)

    composition = create_session_paper_composition(
        at=NOW, event_sink=sink, position_quantity_source=quantity,
        position_average_cost_source=lambda _symbol: D("5"),
    )
    try:
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service, composition.order_command_factory,
            mode="PAPER", enabled=True, order_book=composition.order_book,
            position_quantity_source=quantity,
            protection_amender=composition.gateway.amend_protective_stop,
        )
        runtime = QuickScalperPaperRuntimeAdapter(
            config=QuickScalperConfig(enabled=True), bridge=bridge,
            order_book=composition.order_book, account_context_source=account,
            position_quantity_source=quantity, execution_quote_source=Quotes(quote()),
            clock=lambda: NOW,
        )
        holder["runtime"] = runtime
        value = snapshot()
        runtime._snapshots[("FAST", "g1")] = value
        runtime.scalper.observe(value)
        composition.gateway.process_market_event(MarketEvent(
            1, NOW, "FAST", "feedback", MarketEventType.QUOTE,
            QuotePayload(D("4.99"), D("5"), D("120"), D("120")),
        ))
        assert quantity("FAST") == D("120")
        working = composition.order_book.open_orders_for_symbol("FAST")
        assert sorted(o.request.execution_reason for o in working) == ["ENTRY", "SCALP_TARGET", "STOP"]
        assert len(composition.order_book.history()) == 4
        assert pipeline.paper_account_projection.snapshot.current_cash == D("99400")
        target = next(o for o in working if o.request.execution_reason == "SCALP_TARGET")
        price = max(target.request.limit_price, D("5.30"))
        composition.gateway.process_market_event(MarketEvent(
            2, NOW, "FAST", "feedback", MarketEventType.QUOTE,
            QuotePayload(price, price + D("0.01"), D("120"), D("120")),
        ))
        assert quantity("FAST") == 0
        assert not composition.order_book.open_orders_for_symbol("FAST")
        assert runtime.ownership.owner("FAST") is None
        assert all(o.status in {"FILLED", "CANCELLED"} for o in pipeline.order_projection.snapshot.orders)
        assert pipeline.paper_account_projection.snapshot.current_cash == D("99400") + price * 120
        assert pipeline.paper_account_projection.snapshot.realized_pnl == (price - 5) * 120
    finally:
        composition.close()


@pytest.mark.parametrize("cancel_fails", [False, True])
def test_restart_flat_lifecycle_cleans_orphan_exit_without_releasing_on_failure(cancel_fails, monkeypatch):
    composition = create_session_paper_composition(at=NOW)
    try:
        for order in (
            paper_order(order_id="entry", side=OrderSide.BUY, quantity="120", filled="120",
                        status=OrderStatus.FILLED, reason="ENTRY"),
            paper_order(order_id="target", side=OrderSide.SELL, quantity="120", filled="120",
                        status=OrderStatus.FILLED, reason="SCALP_TARGET"),
            paper_order(order_id="orphan", side=OrderSide.SELL, quantity="120", filled="0",
                        status=OrderStatus.ACCEPTED, reason="STOP"),
            paper_order(order_id="unrelated", side=OrderSide.SELL, quantity="120", filled="0",
                        status=OrderStatus.ACCEPTED, reason="STOP", lifecycle="other-life"),
        ):
            composition.order_book.submit(order)
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service, composition.order_command_factory,
            mode="PAPER", enabled=True, order_book=composition.order_book,
        )
        if cancel_fails:
            monkeypatch.setattr(AutonomousPaperExecutionBridge, "_cancel_working_order", lambda self, _order: False)
        runtime = QuickScalperPaperRuntimeAdapter(
            config=QuickScalperConfig(enabled=True), bridge=bridge,
            order_book=composition.order_book, account_context_source=account,
            position_quantity_source=lambda _symbol: D("0"),
            execution_quote_source=Quotes(quote()), clock=lambda: NOW,
        )
        assert composition.order_book.get("orphan").is_terminal is not cancel_fails
        assert not composition.order_book.get("unrelated").is_terminal
        assert (runtime.ownership.owner("FAST") is None) is not cancel_fails
    finally:
        composition.close()


def test_flat_cleanup_does_not_cancel_protection_for_open_inventory():
    composition = create_session_paper_composition(at=NOW)
    try:
        for order in (
            paper_order(order_id="entry", side=OrderSide.BUY, quantity="120", filled="120",
                        status=OrderStatus.FILLED, reason="ENTRY"),
            paper_order(order_id="partial", side=OrderSide.SELL, quantity="60", filled="60",
                        status=OrderStatus.FILLED, reason="SCALP_TARGET"),
            paper_order(order_id="stop", side=OrderSide.SELL, quantity="60", filled="0",
                        status=OrderStatus.ACCEPTED, reason="STOP"),
        ):
            composition.order_book.submit(order)
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service, composition.order_command_factory,
            mode="PAPER", enabled=True, order_book=composition.order_book,
        )
        assert bridge.cancel_flat_lifecycle_orders("FAST", "life", strategy_owner="QUICK_SCALPER") == ()
        assert not composition.order_book.get("stop").is_terminal
    finally:
        composition.close()

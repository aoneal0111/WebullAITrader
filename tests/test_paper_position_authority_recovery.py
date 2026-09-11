from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.composition.runtime_event_sink import CompositeRuntimeEventSink
from app.momentum_scanner import AssetClass
from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import OperationsBus
from app.paper_trading.fill_models import Fill
from app.paper_trading.models import (
    PaperFill,
)
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    TimeInForce,
)
from app.paper_trading.command_composition import create_paper_trading_command_composition
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.read_models.position_projection import PositionProjection


NOW = datetime(2026, 9, 10, 21, 5, tzinfo=UTC)


def _paper_fill(
    request_id: str,
    *,
    side: str,
    quantity: str,
    price: str,
    timestamp: datetime = NOW,
) -> PaperFill:
    amount = Decimal(quantity)
    value = Decimal(price)
    return PaperFill(
        request_id=request_id,
        symbol="DBGI",
        side=side,
        quantity=amount,
        fill_price=value,
        notional=amount * value,
        realized_pnl=Decimal("0"),
        timestamp=timestamp,
    )


def _event(sequence: int, fill: PaperFill) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=fill.timestamp,
        event_type="FILL",
        message=f"{fill.side} fill {fill.request_id}",
        cycle=sequence,
        symbol=fill.symbol,
        fill=fill,
    )


def _restored_buy_order() -> object:
    fill = Fill(
        fill_id="dbgi-fill-1",
        order_id="dbgi-entry",
        quantity=Decimal("575"),
        price=Decimal("7.386313043478260869565217391"),
        timestamp=NOW,
    )
    return SimpleNamespace(
        symbol="DBGI",
        request=SimpleNamespace(
            side="BUY",
            strategy_lifecycle_id="dbgi-life-1",
            structural_stop_price=None,
            metadata={"structural_stop": "7.24"},
        ),
        fills=(fill,),
    )


def _authoritative_filled_order() -> PaperOrder:
    request = OrderRequest(
        symbol="DBGI",
        asset_class=AssetClass.STOCK,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("575"),
        limit_price=Decimal("7.413705"),
        time_in_force=TimeInForce.DAY,
        strategy_lifecycle_id="dbgi-life-1",
        metadata={"structural_stop": "7.24", "opportunity_id": "dbgi-op"},
    )
    fill = Fill(
        fill_id="dbgi-fill-1",
        order_id="dbgi-entry",
        quantity=Decimal("575"),
        price=Decimal("7.386313043478260869565217391"),
        timestamp=NOW,
    )
    return PaperOrder(
        order_id="dbgi-entry",
        request=request,
        status=OrderStatus.FILLED,
        created_at=NOW,
        updated_at=NOW,
        filled_quantity=Decimal("575"),
        average_fill_price=fill.price,
        fills=(fill,),
    )
def test_restored_authority_seeds_projection_before_sell_fill() -> None:
    projection = PositionProjection(OperationsBus())

    projection.reconcile_from_paper_orders((_restored_buy_order(),))
    assert Decimal(projection.position_for_symbol("DBGI").quantity) == Decimal("575")

    projection(_event(1, _paper_fill(
        "dbgi-sell-1", side="SELL", quantity="575", price="7.24",
    )))

    assert Decimal(projection.position_for_symbol("DBGI").quantity) == Decimal("0")
    assert projection.health == "HEALTHY"


def test_reconciliation_is_idempotent_for_duplicate_authority_and_events() -> None:
    projection = PositionProjection(OperationsBus())
    buy = _paper_fill("dbgi-buy-1", side="BUY", quantity="575", price="7.40")
    sell = _paper_fill("dbgi-sell-2", side="SELL", quantity="575", price="7.24")

    projection(_event(1, buy))
    projection(_event(1, buy))
    assert Decimal(projection.position_for_symbol("DBGI").quantity) == Decimal("575")

    # A restored authoritative seed is also idempotent, and a duplicate
    # protective fill must not create a short position.
    order = _restored_buy_order()
    projection.reconcile_from_paper_orders((order,))
    projection.reconcile_from_paper_orders((order,))
    projection(_event(2, sell))
    projection(_event(3, sell))
    assert Decimal(projection.position_for_symbol("DBGI").quantity) == Decimal("0")


def test_projection_sink_failure_is_degraded_and_does_not_stop_other_sinks(caplog) -> None:
    class FailingProjection:
        health = "HEALTHY"

        def __init__(self) -> None:
            self.degraded = False

        def __call__(self, _event) -> None:
            raise ValueError("sell fill cannot exceed the projected long position")

        def mark_degraded(self) -> None:
            self.degraded = True
            self.health = "DEGRADED"

    received = []
    projection = FailingProjection()
    sink = CompositeRuntimeEventSink((projection, received.append))
    event = _event(1, _paper_fill(
        "dbgi-sell-bad", side="SELL", quantity="575", price="7.24",
    ))

    sink(event)

    assert projection.degraded is True
    assert received == [event]
    assert "runtime projection sink failed" in caplog.text


def test_restored_authoritative_position_gets_one_correlated_stop() -> None:
    order_book = PaperOrderBook()
    order_book.restore(_authoritative_filled_order())
    composition = create_paper_trading_command_composition(order_book=order_book)
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=order_book,
        position_quantity_source=lambda _symbol: Decimal("575"),
    )

    assert bridge.reconcile().value == "READY"
    assert bridge.reconcile_protection() == ("DBGI",)
    stops = [
        order for order in order_book.open_orders_for_symbol("DBGI")
        if order.request.side is OrderSide.SELL
    ]
    assert len(stops) == 1
    assert stops[0].request.order_type is OrderType.STOP
    assert stops[0].request.stop_price == Decimal("7.24")
    assert stops[0].remaining_quantity == Decimal("575")
    composition.close()

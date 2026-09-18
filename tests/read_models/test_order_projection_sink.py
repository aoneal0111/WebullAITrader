from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsOrder,
)
from app.read_models.order_projection import OrderProjection
from app.read_models.orders import OrderReadModel


NOW = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)


def event(
    *,
    sequence: int = 1,
    order_id: str | None = "order-1",
    symbol: str = "AAPL",
    status: str = "ACCEPTED",
    timestamp: datetime = NOW,
) -> PaperRuntimeEvent:
    order = (
        OperationsOrder(
            order_id=order_id,
            symbol=symbol,
            side="BUY",
            quantity="10",
            status=status,
            updated_at=timestamp,
        )
        if order_id is not None
        else None
    )
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=timestamp,
        event_type="DECISION_PROCESSED",
        message=f"Processed decision for {symbol}.",
        cycle=1,
        symbol=symbol,
        order=order,
    )


def test_runtime_order_event_updates_projection_and_application_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = OrderProjection(bus)

    projection(event())

    projected = OrderReadModel(
        order_id="order-1",
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status="ACCEPTED",
        updated_at=NOW,
    )
    assert projection.snapshot.orders == (projected,)
    assert store.snapshot().order_projection.orders == (projected,)
    assert store.snapshot().orders[0].order_id == "order-1"


def test_projection_preserves_authoritative_execution_display_fields() -> None:
    bus = OperationsBus()
    projection = OrderProjection(bus)
    order = OperationsOrder(
        order_id="stop-1", symbol="PMI", side="SELL", quantity="400",
        status="PARTIALLY_FILLED", updated_at=NOW, order_type="STOP",
        stop_price="5.03", filled_quantity="150", remaining_quantity="250",
        average_fill_price="4.50", submitted_at=NOW,
        lifecycle_id="warrior-pmi", execution_reason="STOP",
        execution_source="paper-order-gateway",
    )
    projection(PaperRuntimeEvent(
        sequence=1, timestamp=NOW, event_type="ORDER_PARTIALLY_FILLED",
        message="partial", cycle=1, symbol="PMI", order=order,
    ))

    assert projection.snapshot.orders[0] == OrderReadModel(
        order_id="stop-1", symbol="PMI", side="SELL", quantity="400",
        status="PARTIALLY_FILLED", updated_at=NOW, order_type="STOP",
        stop_price="5.03", filled_quantity="150", remaining_quantity="250",
        average_fill_price="4.50", submitted_at=NOW,
        lifecycle_id="warrior-pmi", execution_reason="STOP",
        execution_source="paper-order-gateway",
    )


def test_projection_ignores_events_without_explicit_order_facts() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = OrderProjection(bus)

    projection(event(order_id=None))

    assert projection.snapshot.orders == ()
    assert store.snapshot().revision == 0


def test_projection_upserts_by_order_id_and_sorts_newest_first() -> None:
    bus = OperationsBus()
    projection = OrderProjection(bus)
    later = NOW + timedelta(minutes=1)

    projection(event(order_id="order-1"))
    projection(
        event(
            sequence=2,
            order_id="order-2",
            symbol="MSFT",
            timestamp=later,
        )
    )
    projection(
        event(
            sequence=3,
            order_id="order-1",
            status="FILLED",
            timestamp=later + timedelta(minutes=1),
        )
    )

    assert tuple(
        (order.order_id, order.status)
        for order in projection.snapshot.orders
    ) == (
        ("order-1", "FILLED"),
        ("order-2", "ACCEPTED"),
    )


def test_duplicate_order_fact_does_not_republish_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = OrderProjection(bus)
    source = event()

    projection(source)
    revision = store.snapshot().revision
    projection(source)

    assert store.snapshot().revision == revision


def test_projection_rejects_wrong_event_type() -> None:
    projection = OrderProjection(OperationsBus())

    with pytest.raises(TypeError, match="event must be a PaperRuntimeEvent"):
        projection(object())  # type: ignore[arg-type]


def _historical_order(order_id: str, status: str, *, updated_at=NOW):
    return SimpleNamespace(
        order_id=order_id,
        symbol="AAPL",
        status=SimpleNamespace(value=status),
        created_at=NOW,
        updated_at=updated_at,
        filled_quantity=Decimal("10") if status == "FILLED" else Decimal("0"),
        remaining_quantity=Decimal("0") if status == "FILLED" else Decimal("10"),
        average_fill_price=Decimal("10") if status == "FILLED" else None,
        request=SimpleNamespace(
            side=SimpleNamespace(value="BUY"),
            quantity=Decimal("10"),
            order_type=SimpleNamespace(value="LIMIT"),
            limit_price=Decimal("10"),
            stop_price=None,
            strategy_lifecycle_id=f"life-{order_id}",
            execution_reason="ENTRY",
        ),
    )


def test_historical_reconciliation_publishes_terminal_orders_only() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = OrderProjection(bus)

    projection.reconcile_historical_terminal_orders((
        _historical_order("filled-old", "FILLED"),
        _historical_order("cancelled-old", "CANCELLED"),
        _historical_order("working-old", "WORKING"),
    ))

    assert tuple(order.order_id for order in projection.snapshot.orders) == (
        "cancelled-old", "filled-old",
    )
    assert tuple(order.order_id for order in store.snapshot().order_projection.orders) == (
        "cancelled-old", "filled-old",
    )
    assert all(
        order.execution_source == "paper-execution-history"
        for order in projection.snapshot.orders
    )


def test_live_order_upserts_over_historical_terminal_snapshot() -> None:
    projection = OrderProjection(OperationsBus())
    projection.reconcile_historical_terminal_orders((
        _historical_order("order-1", "CANCELLED"),
    ))

    projection(event(order_id="order-1", status="FILLED", timestamp=NOW + timedelta(minutes=1)))

    assert projection.snapshot.orders[0].status == "FILLED"
    assert projection.snapshot.orders[0].execution_source is None

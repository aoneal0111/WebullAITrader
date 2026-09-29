from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import ApplicationStateStore, OperationsBus
from app.paper_trading.models import PaperFill
from app.read_models.position_projection import PositionProjection


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def replace_event_source(event: PaperRuntimeEvent, source: str) -> PaperRuntimeEvent:
    return replace(event, source=source)


def fill_event(
    *,
    sequence: int,
    request_id: str,
    symbol: str = "AAPL",
    side: str = "BUY",
    quantity: str = "10",
    fill_price: str = "100",
    realized_pnl: str = "0",
    mark_price: str | None = "110",
    timestamp: datetime | None = None,
) -> PaperRuntimeEvent:
    occurred_at = timestamp or NOW + timedelta(minutes=sequence)
    quantity_value = Decimal(quantity)
    price_value = Decimal(fill_price)
    fill = PaperFill(
        request_id=request_id,
        symbol=symbol,
        side=side,
        quantity=quantity_value,
        fill_price=price_value,
        notional=quantity_value * price_value,
        realized_pnl=Decimal(realized_pnl),
        timestamp=occurred_at,
    )
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=occurred_at,
        event_type="FILL",
        message=f"Filled {request_id}.",
        cycle=sequence,
        symbol=symbol,
        fill=fill,
        mark_price=(
            Decimal(mark_price)
            if mark_price is not None
            else None
        ),
    )


def test_opening_position_projects_cost_and_valuation() -> None:
    projection = PositionProjection(OperationsBus(), account_id="paper-1")

    projection(fill_event(sequence=1, request_id="fill-1"))

    position = projection.snapshot.positions[0]
    assert position.account_id == "paper-1"
    assert position.symbol == "AAPL"
    assert position.quantity == "10"
    assert position.average_cost == "100"
    assert position.realized_gain_loss == "0"
    assert position.unrealized_gain_loss == "100"
    assert position.market_value == "1100"
    assert position.exposure == "1100"
    assert projection.position_for_symbol("aapl") is position
    assert projection.position_for_symbol("MSFT") is None


def test_increasing_position_recalculates_weighted_average_cost() -> None:
    projection = PositionProjection(OperationsBus())
    projection(fill_event(sequence=1, request_id="fill-1"))

    projection(
        fill_event(
            sequence=2,
            request_id="fill-2",
            fill_price="120",
            mark_price="125",
        )
    )

    position = projection.snapshot.positions[0]
    assert position.quantity == "20"
    assert position.average_cost == "110"
    assert position.market_value == "2500"
    assert position.unrealized_gain_loss == "300"


def test_partial_close_preserves_cost_and_accumulates_realized_pnl() -> None:
    projection = PositionProjection(OperationsBus())
    projection(fill_event(sequence=1, request_id="fill-1"))
    projection(
        fill_event(
            sequence=2,
            request_id="fill-2",
            fill_price="120",
            mark_price="125",
        )
    )

    projection(
        fill_event(
            sequence=3,
            request_id="fill-3",
            side="SELL",
            quantity="5",
            fill_price="130",
            realized_pnl="100",
            mark_price="128",
        )
    )

    position = projection.snapshot.positions[0]
    assert position.quantity == "15"
    assert position.average_cost == "110"
    assert position.realized_gain_loss == "100"
    assert position.market_value == "1920"
    assert position.unrealized_gain_loss == "270"


def test_full_close_retains_zero_state_and_realized_history() -> None:
    projection = PositionProjection(OperationsBus())
    projection(fill_event(sequence=1, request_id="fill-1"))
    projection(
        fill_event(
            sequence=2,
            request_id="fill-2",
            side="SELL",
            quantity="10",
            fill_price="130",
            realized_pnl="300",
            mark_price=None,
        )
    )

    position = projection.snapshot.positions[0]
    assert position.quantity == "0"
    assert position.average_cost == "100"
    assert position.realized_gain_loss == "300"
    assert position.market_value == "0"
    assert position.unrealized_gain_loss == "0"
    assert position.exposure == "0"


def test_multiple_symbols_are_projected_in_stable_symbol_order() -> None:
    projection = PositionProjection(OperationsBus())

    projection(
        fill_event(
            sequence=1,
            request_id="msft-1",
            symbol="MSFT",
        )
    )
    projection(
        fill_event(
            sequence=2,
            request_id="aapl-1",
            symbol="AAPL",
        )
    )

    assert tuple(
        position.symbol
        for position in projection.snapshot.positions
    ) == ("AAPL", "MSFT")


def test_unknown_mark_leaves_valuation_and_exposure_unknown() -> None:
    projection = PositionProjection(OperationsBus())

    projection(
        fill_event(
            sequence=1,
            request_id="fill-1",
            mark_price=None,
        )
    )

    position = projection.snapshot.positions[0]
    assert position.market_value is None
    assert position.unrealized_gain_loss is None
    assert position.exposure is None


def test_mark_before_authority_reconciliation_is_replayed() -> None:
    projection = PositionProjection(OperationsBus())
    mark = PaperRuntimeEvent(
        sequence=1,
        timestamp=NOW,
        event_type="MARK_UPDATED",
        message="early mark",
        cycle=0,
        symbol="BNKK",
        source="market-data",
        mark_price=Decimal("2.50"),
    )
    projection(mark)

    restored_fill = type("Fill", (), {
        "quantity": Decimal("417"),
        "price": Decimal("2.040"),
        "timestamp": NOW,
    })()
    restored_order = type("Order", (), {
        "symbol": "BNKK",
        "request": type("Request", (), {"side": "BUY"})(),
        "fills": (restored_fill,),
    })()
    projection.reconcile_from_paper_orders((restored_order,))

    position = projection.position_for_symbol("BNKK")
    assert position is not None
    assert position.quantity == "417"
    assert position.average_cost == "2.040"
    assert position.market_value == "1042.50"
    assert position.unrealized_gain_loss == "191.820"
    assert position.exposure == "1042.50"


def test_incomplete_paper_replay_fill_does_not_degrade_projection() -> None:
    projection = PositionProjection(OperationsBus())
    historical_sell = fill_event(
        sequence=1,
        request_id="legacy-sell",
        symbol="GDC",
        side="SELL",
        quantity="1100",
        fill_price="1.39",
    )
    historical_sell = replace_event_source(
        historical_sell,
        "paper-execution-replay",
    )

    projection(historical_sell)

    assert projection.health == "HEALTHY"
    assert projection.snapshot.positions == ()


def test_reconciliation_remains_authoritative_after_incomplete_replay() -> None:
    projection = PositionProjection(OperationsBus())
    historical_sell = replace_event_source(
        fill_event(
            sequence=1,
            request_id="legacy-sell",
            symbol="GDC",
            side="SELL",
            quantity="1100",
            fill_price="1.39",
        ),
        "paper-execution-replay",
    )
    projection(historical_sell)

    buy_fill = type("Fill", (), {
        "quantity": Decimal("1100"),
        "price": Decimal("1.50"),
        "timestamp": NOW,
    })()
    sell_fill = type("Fill", (), {
        "quantity": Decimal("1100"),
        "price": Decimal("1.39"),
        "timestamp": NOW + timedelta(seconds=1),
    })()
    order_factory = lambda side, fills: type("Order", (), {
        "symbol": "GDC",
        "request": type("Request", (), {"side": side})(),
        "fills": fills,
    })()
    projection.reconcile_from_paper_orders((
        order_factory("BUY", (buy_fill,)),
        order_factory("SELL", (sell_fill,)),
    ))

    assert projection.health == "HEALTHY"
    assert projection.snapshot.positions == ()


def test_live_malformed_sell_still_raises_strict_invariant() -> None:
    projection = PositionProjection(OperationsBus())

    with pytest.raises(
        ValueError,
        match="sell fill cannot exceed the projected long position",
    ):
        projection(fill_event(
            sequence=1,
            request_id="live-sell",
            side="SELL",
            quantity="1",
            fill_price="100",
            mark_price=None,
        ))


def test_duplicate_and_out_of_order_events_are_ignored() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = PositionProjection(bus)
    first = fill_event(sequence=1, request_id="fill-1")
    later = fill_event(sequence=3, request_id="fill-2")

    projection(first)
    projection(first)
    projection(later)
    revision = store.snapshot().revision
    projection(
        fill_event(
            sequence=2,
            request_id="late-fill",
            fill_price="50",
        )
    )
    projection(
        fill_event(
            sequence=4,
            request_id="fill-2",
            fill_price="999",
        )
    )

    assert projection.snapshot.positions[0].quantity == "20"
    assert projection.snapshot.positions[0].average_cost == "100"
    assert store.snapshot().revision == revision


def test_projection_publishes_immutable_application_position_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = PositionProjection(bus, account_id="paper-1")

    projection(fill_event(sequence=1, request_id="fill-1"))

    state = store.snapshot()
    assert state.position_projection == projection.snapshot
    assert state.positions[0].symbol == "AAPL"
    assert state.positions[0].market_value == "1100"

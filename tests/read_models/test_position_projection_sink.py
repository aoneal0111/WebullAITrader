from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import ApplicationStateStore, OperationsBus
from app.paper_trading.models import PaperFill
from app.read_models.position_projection import PositionProjection


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


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

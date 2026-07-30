from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.composition.runtime_event_sink import CompositeRuntimeEventSink
from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsOrder,
    PortfolioUpdated,
)
from app.paper_trading.models import PaperFill
from app.read_models.order_projection import OrderProjection
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot
from app.read_models.portfolio import PortfolioSummary
from app.read_models.portfolio_projection import PortfolioProjection
from app.read_models.position_projection import PositionProjection
from app.read_models.positions import (
    PositionReadModel,
    PositionsReadModelSnapshot,
)


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


class PositionSource:
    def __init__(
        self,
        snapshot: PositionsReadModelSnapshot | None = None,
    ) -> None:
        self.snapshot = snapshot or PositionsReadModelSnapshot.initial()


class OrderSource:
    def __init__(
        self,
        snapshot: OrdersReadModelSnapshot | None = None,
    ) -> None:
        self.snapshot = snapshot or OrdersReadModelSnapshot.initial()


def position(
    symbol: str,
    *,
    quantity: str,
    average_cost: str,
    market_value: str | None,
    unrealized: str | None,
    realized: str | None = "0",
    exposure: str | None,
) -> PositionReadModel:
    return PositionReadModel(
        account_id="paper",
        symbol=symbol,
        asset_type="EQUITY",
        quantity=quantity,
        average_cost=average_cost,
        market_value=market_value,
        unrealized_gain_loss=unrealized,
        realized_gain_loss=realized,
        currency="USD",
        updated_at=NOW,
        exposure=exposure,
    )


def order(order_id: str, status: str) -> OrderReadModel:
    return OrderReadModel(
        order_id=order_id,
        symbol="AAPL",
        side="BUY",
        quantity="1",
        status=status,
        updated_at=NOW,
    )


def runtime_event(
    sequence: int,
    *,
    symbol: str | None = None,
    mark_price: Decimal | None = None,
    fill: PaperFill | None = None,
    operations_order: OperationsOrder | None = None,
) -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=NOW + timedelta(seconds=sequence),
        event_type="MARK_UPDATED" if mark_price is not None else "UPDATED",
        message="Structured runtime facts updated.",
        cycle=1,
        symbol=symbol,
        mark_price=mark_price,
        fill=fill,
        order=operations_order,
    )


def projection_from(
    positions: tuple[PositionReadModel, ...] = (),
    orders: tuple[OrderReadModel, ...] = (),
) -> tuple[PortfolioProjection, PositionSource, OrderSource]:
    position_source = PositionSource(
        PositionsReadModelSnapshot(positions=positions)
    )
    order_source = OrderSource(OrdersReadModelSnapshot(orders=orders))
    projection = PortfolioProjection(
        OperationsBus(),
        position_projection=position_source,
        order_projection=order_source,
    )
    return projection, position_source, order_source


def test_empty_portfolio_is_known_zero_and_emits_no_redundant_update() -> None:
    bus = OperationsBus()
    updates = []
    bus.subscribe(PortfolioUpdated, updates.append)
    projection = PortfolioProjection(
        bus,
        position_projection=PositionSource(),
        order_projection=OrderSource(),
    )

    projection(runtime_event(1))

    assert projection.snapshot == PortfolioSummary.initial()
    assert updates == []


def test_single_long_position_derives_summary_and_highlights() -> None:
    projection, _, _ = projection_from(
        (
            position(
                "AAPL",
                quantity="10",
                average_cost="100",
                market_value="1200",
                unrealized="200",
                realized="50",
                exposure="1200",
            ),
        )
    )

    projection(runtime_event(1))

    summary = projection.snapshot
    assert summary.total_market_value == "1200"
    assert summary.total_cost_basis == "1000"
    assert summary.realized_pnl == "50"
    assert summary.unrealized_pnl == "200"
    assert summary.total_pnl == "250"
    assert summary.gross_exposure == "1200"
    assert summary.long_exposure == "1200"
    assert summary.short_exposure == "0"
    assert summary.open_positions == 1
    assert summary.winning_positions == 1
    assert summary.losing_positions == 0
    assert summary.largest_position.symbol == "AAPL"
    with pytest.raises(FrozenInstanceError):
        summary.open_positions = 2  # type: ignore[misc]


def test_multiple_mixed_positions_derive_directional_exposure() -> None:
    projection, _, _ = projection_from(
        (
            position(
                "AAPL",
                quantity="10",
                average_cost="100",
                market_value="1200",
                unrealized="200",
                realized="50",
                exposure="1200",
            ),
            position(
                "MSFT",
                quantity="-5",
                average_cost="200",
                market_value="-900",
                unrealized="100",
                realized="30",
                exposure="900",
            ),
            position(
                "TSLA",
                quantity="2",
                average_cost="50",
                market_value="80",
                unrealized="-20",
                exposure="80",
            ),
        )
    )

    projection(runtime_event(1))

    summary = projection.snapshot
    assert summary.total_market_value == "380"
    assert summary.total_cost_basis == "2100"
    assert summary.realized_pnl == "80"
    assert summary.unrealized_pnl == "280"
    assert summary.gross_exposure == "2180"
    assert summary.long_exposure == "1280"
    assert summary.short_exposure == "900"
    assert summary.open_positions == 3
    assert summary.winning_positions == 2
    assert summary.losing_positions == 1
    assert summary.largest_position.symbol == "AAPL"
    assert summary.largest_unrealized_gain.value == "200"
    assert summary.largest_unrealized_loss.value == "-20"


def test_unknown_position_values_propagate_to_aggregate_values() -> None:
    projection, _, _ = projection_from(
        (
            position(
                "AAPL",
                quantity="10",
                average_cost="100",
                market_value=None,
                unrealized=None,
                exposure=None,
            ),
        )
    )

    projection(runtime_event(1))

    summary = projection.snapshot
    assert summary.total_market_value is None
    assert summary.unrealized_pnl is None
    assert summary.total_pnl is None
    assert summary.gross_exposure is None
    assert summary.long_exposure is None
    assert summary.largest_position is None
    assert summary.winning_positions is None
    assert summary.losing_positions is None


def test_working_order_count_uses_projected_order_statuses() -> None:
    projection, _, _ = projection_from(
        orders=(
            order("one", "SUBMITTED"),
            order("two", "ACCEPTED"),
            order("three", "PARTIALLY_FILLED"),
            order("four", "FILLED"),
            order("five", "REJECTED"),
            order("six", "CANCELLED"),
        )
    )

    projection(runtime_event(1))

    assert projection.snapshot.working_orders == 3


def test_realized_pnl_updates_after_partial_close() -> None:
    bus = OperationsBus()
    positions = PositionProjection(bus)
    orders = OrderProjection(bus)
    portfolio = PortfolioProjection(
        bus,
        position_projection=positions,
        order_projection=orders,
    )
    sink = CompositeRuntimeEventSink((positions, orders, portfolio))
    opening = PaperFill(
        request_id="fill-1",
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("10"),
        fill_price=Decimal("100"),
        notional=Decimal("1000"),
        realized_pnl=Decimal("0"),
        timestamp=NOW + timedelta(seconds=1),
    )
    closing = PaperFill(
        request_id="fill-2",
        symbol="AAPL",
        side="SELL",
        quantity=Decimal("4"),
        fill_price=Decimal("110"),
        notional=Decimal("440"),
        realized_pnl=Decimal("40"),
        timestamp=NOW + timedelta(seconds=2),
    )

    sink(runtime_event(1, symbol="AAPL", mark_price=Decimal("100"), fill=opening))
    sink(runtime_event(2, symbol="AAPL", mark_price=Decimal("110"), fill=closing))

    assert portfolio.snapshot.realized_pnl == "40"
    assert portfolio.snapshot.unrealized_pnl == "60"
    assert portfolio.snapshot.total_pnl == "100"
    assert portfolio.snapshot.total_cost_basis == "600"


def test_market_mark_updates_position_then_portfolio_valuation() -> None:
    bus = OperationsBus()
    positions = PositionProjection(bus)
    orders = OrderProjection(bus)
    portfolio = PortfolioProjection(
        bus,
        position_projection=positions,
        order_projection=orders,
    )
    sink = CompositeRuntimeEventSink((positions, orders, portfolio))
    opening = PaperFill(
        request_id="fill-1",
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("10"),
        fill_price=Decimal("100"),
        notional=Decimal("1000"),
        realized_pnl=Decimal("0"),
        timestamp=NOW + timedelta(seconds=1),
    )
    sink(runtime_event(1, symbol="AAPL", mark_price=Decimal("100"), fill=opening))

    sink(runtime_event(2, symbol="AAPL", mark_price=Decimal("120")))

    assert positions.snapshot.positions[0].market_value == "1200"
    assert portfolio.snapshot.total_market_value == "1200"
    assert portfolio.snapshot.unrealized_pnl == "200"
    assert portfolio.snapshot.gross_exposure == "1200"


def test_duplicate_events_are_idempotent() -> None:
    bus = OperationsBus()
    updates = []
    bus.subscribe(PortfolioUpdated, updates.append)
    positions = PositionProjection(bus)
    orders = OrderProjection(bus)
    portfolio = PortfolioProjection(
        bus,
        position_projection=positions,
        order_projection=orders,
    )
    sink = CompositeRuntimeEventSink((positions, orders, portfolio))
    opening = PaperFill(
        request_id="fill-1",
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("1"),
        fill_price=Decimal("100"),
        notional=Decimal("100"),
        realized_pnl=Decimal("0"),
        timestamp=NOW + timedelta(seconds=1),
    )
    event = runtime_event(
        1,
        symbol="AAPL",
        mark_price=Decimal("100"),
        fill=opening,
    )

    sink(event)
    sink(event)

    assert len(updates) == 1
    assert portfolio.snapshot.open_positions == 1


def test_deterministic_replay_produces_identical_summary() -> None:
    opening = PaperFill(
        request_id="fill-1",
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("2"),
        fill_price=Decimal("100"),
        notional=Decimal("200"),
        realized_pnl=Decimal("0"),
        timestamp=NOW + timedelta(seconds=1),
    )
    events = (
        runtime_event(
            1,
            symbol="AAPL",
            mark_price=Decimal("100"),
            fill=opening,
        ),
        runtime_event(2, symbol="AAPL", mark_price=Decimal("125")),
    )

    summaries = []
    for _ in range(2):
        bus = OperationsBus()
        positions = PositionProjection(bus)
        orders = OrderProjection(bus)
        portfolio = PortfolioProjection(
            bus,
            position_projection=positions,
            order_projection=orders,
        )
        sink = CompositeRuntimeEventSink((positions, orders, portfolio))
        for event in events:
            sink(event)
        summaries.append(portfolio.snapshot)

    assert summaries[0] == summaries[1]


def test_application_state_exposes_portfolio_projection() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    position_source = PositionSource(
        PositionsReadModelSnapshot(
            positions=(
                position(
                    "AAPL",
                    quantity="1",
                    average_cost="100",
                    market_value="110",
                    unrealized="10",
                    exposure="110",
                ),
            )
        )
    )
    projection = PortfolioProjection(
        bus,
        position_projection=position_source,
        order_projection=OrderSource(),
    )

    projection(runtime_event(1))

    assert store.snapshot().portfolio_projection == projection.snapshot

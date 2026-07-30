from __future__ import annotations

from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Protocol

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    OperationsBus,
    OperationsPortfolioHighlight,
    OperationsPortfolioSummary,
    PortfolioUpdated,
)
from app.read_models.orders import OrdersReadModelSnapshot
from app.read_models.portfolio import PortfolioHighlight, PortfolioSummary
from app.read_models.positions import PositionsReadModelSnapshot


ZERO = Decimal("0")
_WORKING_STATUSES = frozenset(
    {
        "ACCEPTED",
        "ACKNOWLEDGED",
        "NEW",
        "OPEN",
        "PARTIAL_FILL",
        "PARTIALLY_FILLED",
        "PENDING",
        "SUBMITTED",
        "WORKING",
    }
)


class PositionProjectionSource(Protocol):
    @property
    def snapshot(self) -> PositionsReadModelSnapshot: ...


class OrderProjectionSource(Protocol):
    @property
    def snapshot(self) -> OrdersReadModelSnapshot: ...


class PortfolioProjection:
    """Aggregate existing immutable order and position projections."""

    def __init__(
        self,
        bus: OperationsBus,
        *,
        position_projection: PositionProjectionSource,
        order_projection: OrderProjectionSource,
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        self._bus = bus
        self._position_projection = position_projection
        self._order_projection = order_projection
        self._lock = RLock()
        self._snapshot = PortfolioSummary.initial()

    @property
    def snapshot(self) -> PortfolioSummary:
        with self._lock:
            return self._snapshot

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        positions = self._position_projection.snapshot
        orders = self._order_projection.snapshot
        if not isinstance(positions, PositionsReadModelSnapshot):
            raise TypeError(
                "position projection must expose PositionsReadModelSnapshot"
            )
        if not isinstance(orders, OrdersReadModelSnapshot):
            raise TypeError(
                "order projection must expose OrdersReadModelSnapshot"
            )

        projected = aggregate_portfolio(positions, orders)
        with self._lock:
            if projected == self._snapshot:
                return
            self._snapshot = projected

        self._bus.publish(
            PortfolioUpdated(
                occurred_at=event.timestamp,
                source="paper-runtime-portfolio-projection",
                summary=to_operations_portfolio(projected),
            )
        )


def aggregate_portfolio(
    positions: PositionsReadModelSnapshot,
    orders: OrdersReadModelSnapshot,
) -> PortfolioSummary:
    open_positions = tuple(
        position
        for position in positions.positions
        if _decimal(position.quantity, "quantity") != ZERO
    )
    quantities = {
        position.symbol: _decimal(position.quantity, "quantity")
        for position in open_positions
    }
    cost_basis = sum(
        (
            abs(quantities[position.symbol])
            * _decimal(position.average_cost, "average_cost")
            for position in open_positions
        ),
        ZERO,
    )

    market_values = _known_values(open_positions, "market_value")
    exposures = _known_values(open_positions, "exposure")
    unrealized_values = _known_values(
        open_positions,
        "unrealized_gain_loss",
    )
    realized_values = _known_values(
        positions.positions,
        "realized_gain_loss",
    )

    total_market_value = _sum_known(market_values)
    gross_exposure = _sum_known(exposures)
    realized_pnl = _sum_known(realized_values)
    unrealized_pnl = _sum_known(unrealized_values)
    total_pnl = (
        realized_pnl + unrealized_pnl
        if realized_pnl is not None and unrealized_pnl is not None
        else None
    )
    long_exposure = _directional_exposure(
        open_positions,
        quantities,
        exposures,
        positive=True,
    )
    short_exposure = _directional_exposure(
        open_positions,
        quantities,
        exposures,
        positive=False,
    )

    return PortfolioSummary(
        total_market_value=_text_or_none(total_market_value),
        total_cost_basis=_text(cost_basis),
        realized_pnl=_text_or_none(realized_pnl),
        unrealized_pnl=_text_or_none(unrealized_pnl),
        total_pnl=_text_or_none(total_pnl),
        gross_exposure=_text_or_none(gross_exposure),
        long_exposure=_text_or_none(long_exposure),
        short_exposure=_text_or_none(short_exposure),
        open_positions=len(open_positions),
        working_orders=sum(
            order.status.strip().upper().replace(" ", "_")
            in _WORKING_STATUSES
            for order in orders.orders
        ),
        winning_positions=(
            sum(
                value > ZERO
                for value in unrealized_values.values()
                if value is not None
            )
            if unrealized_pnl is not None
            else None
        ),
        losing_positions=(
            sum(
                value < ZERO
                for value in unrealized_values.values()
                if value is not None
            )
            if unrealized_pnl is not None
            else None
        ),
        largest_position=_largest(
            exposures,
            require_complete=bool(open_positions),
            key=lambda value: value,
        ),
        largest_unrealized_gain=_largest(
            unrealized_values,
            require_complete=bool(open_positions),
            key=lambda value: value if value > ZERO else None,
        ),
        largest_unrealized_loss=_largest(
            unrealized_values,
            require_complete=bool(open_positions),
            key=lambda value: abs(value) if value < ZERO else None,
        ),
    )


def _known_values(positions, field_name: str) -> dict[str, Decimal | None]:
    return {
        position.symbol: (
            _decimal(getattr(position, field_name), field_name)
            if getattr(position, field_name) is not None
            else None
        )
        for position in positions
    }


def _sum_known(values: dict[str, Decimal | None]) -> Decimal | None:
    if any(value is None for value in values.values()):
        return None
    return sum((value for value in values.values() if value is not None), ZERO)


def _directional_exposure(
    positions,
    quantities: dict[str, Decimal],
    exposures: dict[str, Decimal | None],
    *,
    positive: bool,
) -> Decimal | None:
    selected = tuple(
        position
        for position in positions
        if (quantities[position.symbol] > ZERO) is positive
    )
    if any(exposures[position.symbol] is None for position in selected):
        return None
    return sum(
        (
            exposures[position.symbol]
            for position in selected
            if exposures[position.symbol] is not None
        ),
        ZERO,
    )


def _largest(
    values: dict[str, Decimal | None],
    *,
    require_complete: bool,
    key,
) -> PortfolioHighlight | None:
    if require_complete and any(value is None for value in values.values()):
        return None
    candidates = tuple(
        (symbol, value, key(value))
        for symbol, value in values.items()
        if value is not None and key(value) is not None
    )
    if not candidates:
        return None
    symbol, value, _ = max(
        candidates,
        key=lambda item: (item[2], item[0]),
    )
    return PortfolioHighlight(symbol=symbol, value=_text(value))


def _decimal(value: str, field_name: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be Decimal-compatible") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


def _text(value: Decimal) -> str:
    return format(value, "f")


def _text_or_none(value: Decimal | None) -> str | None:
    return _text(value) if value is not None else None


def to_operations_portfolio(
    summary: PortfolioSummary,
) -> OperationsPortfolioSummary:
    return OperationsPortfolioSummary(
        total_market_value=summary.total_market_value,
        total_cost_basis=summary.total_cost_basis,
        realized_pnl=summary.realized_pnl,
        unrealized_pnl=summary.unrealized_pnl,
        total_pnl=summary.total_pnl,
        gross_exposure=summary.gross_exposure,
        long_exposure=summary.long_exposure,
        short_exposure=summary.short_exposure,
        open_positions=summary.open_positions,
        working_orders=summary.working_orders,
        winning_positions=summary.winning_positions,
        losing_positions=summary.losing_positions,
        largest_position=_to_operations_highlight(
            summary.largest_position
        ),
        largest_unrealized_gain=_to_operations_highlight(
            summary.largest_unrealized_gain
        ),
        largest_unrealized_loss=_to_operations_highlight(
            summary.largest_unrealized_loss
        ),
    )


def _to_operations_highlight(
    value: PortfolioHighlight | None,
) -> OperationsPortfolioHighlight | None:
    if value is None:
        return None
    return OperationsPortfolioHighlight(
        symbol=value.symbol,
        value=value.value,
    )


__all__ = [
    "PortfolioProjection",
    "aggregate_portfolio",
    "to_operations_portfolio",
]

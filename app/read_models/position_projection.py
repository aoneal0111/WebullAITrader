from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from threading import RLock

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    OperationsBus,
    OperationsPosition,
    PositionsUpdated,
    ProjectionAuthority,
)
from app.paper_trading.models import PaperFill
from app.read_models.positions.models import (
    PositionReadModel,
    PositionsReadModelSnapshot,
)
from app.read_models.runtime_event_identity import projection_event_id


ZERO = Decimal("0")


class PositionProjection:
    """Fold ordered runtime fill events into immutable position state."""

    def __init__(
        self,
        bus: OperationsBus,
        *,
        account_id: str = "paper",
        asset_type: str = "EQUITY",
        currency: str = "USD",
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")

        self._account_id = _required_text(account_id, "account_id")
        self._asset_type = _required_text(asset_type, "asset_type")
        self._currency = _required_text(currency, "currency")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = PositionsReadModelSnapshot.initial()
        self._processed_fill_ids: frozenset[str] = frozenset()
        self._last_sequence_by_source: dict[str, int] = {}

    @property
    def snapshot(self) -> PositionsReadModelSnapshot:
        with self._lock:
            return self._snapshot

    def position_for_symbol(self, symbol: str) -> PositionReadModel | None:
        """Return current symbol state without scanning historical events."""

        if not isinstance(symbol, str):
            raise TypeError("symbol must be a string")
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("symbol must be non-empty")
        positions = self.snapshot.positions
        low, high = 0, len(positions)
        while low < high:
            middle = (low + high) // 2
            candidate = positions[middle]
            if candidate.symbol < normalized:
                low = middle + 1
            else:
                high = middle
        if low < len(positions) and positions[low].symbol == normalized:
            return positions[low]
        return None

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")

        with self._lock:
            last_sequence = self._last_sequence_by_source.get(
                event.source,
                0,
            )
            if event.sequence <= last_sequence:
                return
            self._last_sequence_by_source[event.source] = event.sequence

            fill = event.fill
            if fill is not None:
                if fill.request_id in self._processed_fill_ids:
                    return
                projected = _reduce_fill(
                    self._snapshot,
                    fill,
                    mark_price=event.mark_price,
                    account_id=self._account_id,
                    asset_type=self._asset_type,
                    currency=self._currency,
                )
                self._processed_fill_ids = (
                    self._processed_fill_ids | {fill.request_id}
                )
                occurred_at = fill.timestamp
            elif event.mark_price is not None and event.symbol is not None:
                projected = _reduce_mark(
                    self._snapshot,
                    symbol=event.symbol,
                    mark_price=event.mark_price,
                    timestamp=event.timestamp,
                )
                occurred_at = event.timestamp
            else:
                return
            if projected == self._snapshot:
                return
            self._snapshot = projected
            positions = tuple(
                _to_operations_position(position)
                for position in projected.positions
            )

        self._bus.publish(
            PositionsUpdated(
                occurred_at=occurred_at,
                event_id=projection_event_id("positions", event),
                source="paper-runtime-position-projection",
                positions=positions,
                projection_authority=ProjectionAuthority.PAPER_EXECUTION,
            )
        )


def _reduce_mark(
    current: PositionsReadModelSnapshot,
    *,
    symbol: str,
    mark_price: Decimal,
    timestamp: datetime,
) -> PositionsReadModelSnapshot:
    by_symbol = {
        position.symbol: position
        for position in current.positions
    }
    existing = by_symbol.get(symbol)
    if existing is None:
        return current
    market_value, unrealized_pnl, exposure = _valuation(
        quantity=Decimal(existing.quantity),
        average_cost=Decimal(existing.average_cost),
        mark_price=mark_price,
    )
    by_symbol[symbol] = replace(
        existing,
        market_value=_optional_decimal_text(market_value),
        unrealized_gain_loss=_optional_decimal_text(unrealized_pnl),
        exposure=_optional_decimal_text(exposure),
        updated_at=timestamp,
    )
    return PositionsReadModelSnapshot(
        positions=tuple(
            sorted(
                by_symbol.values(),
                key=lambda position: position.symbol,
            )
        )
    )


def _reduce_fill(
    current: PositionsReadModelSnapshot,
    fill: PaperFill,
    *,
    mark_price: Decimal | None,
    account_id: str,
    asset_type: str,
    currency: str,
) -> PositionsReadModelSnapshot:
    _validate_fill(fill)
    by_symbol = {
        position.symbol: position
        for position in current.positions
    }
    existing = by_symbol.get(fill.symbol)
    old_quantity = (
        Decimal(existing.quantity)
        if existing is not None
        else ZERO
    )
    old_average_cost = (
        Decimal(existing.average_cost)
        if existing is not None
        else ZERO
    )
    old_realized_pnl = (
        Decimal(existing.realized_gain_loss)
        if existing is not None
        and existing.realized_gain_loss is not None
        else ZERO
    )

    if fill.side == "BUY":
        new_quantity = old_quantity + fill.quantity
        average_cost = (
            (old_quantity * old_average_cost)
            + (fill.quantity * fill.fill_price)
        ) / new_quantity
    elif fill.side == "SELL":
        if existing is None or fill.quantity > old_quantity:
            raise ValueError(
                "sell fill cannot exceed the projected long position"
            )
        new_quantity = old_quantity - fill.quantity
        average_cost = old_average_cost
    else:
        raise ValueError("fill side must be BUY or SELL")

    realized_pnl = old_realized_pnl + fill.realized_pnl
    market_value, unrealized_pnl, exposure = _valuation(
        quantity=new_quantity,
        average_cost=average_cost,
        mark_price=mark_price,
    )
    by_symbol[fill.symbol] = PositionReadModel(
        account_id=account_id,
        symbol=fill.symbol,
        asset_type=asset_type,
        quantity=_decimal_text(new_quantity),
        average_cost=_decimal_text(average_cost),
        market_value=_optional_decimal_text(market_value),
        unrealized_gain_loss=_optional_decimal_text(unrealized_pnl),
        realized_gain_loss=_decimal_text(realized_pnl),
        currency=currency,
        updated_at=fill.timestamp,
        exposure=_optional_decimal_text(exposure),
    )

    return PositionsReadModelSnapshot(
        positions=tuple(
            sorted(
                by_symbol.values(),
                key=lambda position: position.symbol,
            )
        )
    )


def _valuation(
    *,
    quantity: Decimal,
    average_cost: Decimal,
    mark_price: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if quantity == ZERO:
        return ZERO, ZERO, ZERO
    if mark_price is None:
        return None, None, None
    if (
        not isinstance(mark_price, Decimal)
        or not mark_price.is_finite()
        or mark_price <= ZERO
    ):
        raise ValueError("mark_price must be a positive finite Decimal")

    market_value = quantity * mark_price
    unrealized_pnl = (mark_price - average_cost) * quantity
    return market_value, unrealized_pnl, abs(market_value)


def _validate_fill(fill: PaperFill) -> None:
    if not isinstance(fill, PaperFill):
        raise TypeError("fill must be a PaperFill")
    _required_text(fill.request_id, "fill request_id")
    _required_text(fill.symbol, "fill symbol")
    if fill.symbol != fill.symbol.strip().upper():
        raise ValueError("fill symbol must be normalized")
    for field_name in (
        "quantity",
        "fill_price",
        "notional",
    ):
        value = getattr(fill, field_name)
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or value <= ZERO
        ):
            raise ValueError(
                f"fill {field_name} must be a positive finite Decimal"
            )
    if fill.notional != fill.quantity * fill.fill_price:
        raise ValueError("fill notional must equal quantity times fill price")
    if (
        not isinstance(fill.realized_pnl, Decimal)
        or not fill.realized_pnl.is_finite()
    ):
        raise ValueError("fill realized_pnl must be a finite Decimal")
    if fill.timestamp.tzinfo is None:
        raise ValueError("fill timestamp must be timezone-aware")


def _to_operations_position(
    position: PositionReadModel,
) -> OperationsPosition:
    return OperationsPosition(
        account_id=position.account_id,
        symbol=position.symbol,
        asset_type=position.asset_type,
        quantity=position.quantity,
        average_cost=position.average_cost,
        market_value=position.market_value,
        unrealized_gain_loss=position.unrealized_gain_loss,
        realized_gain_loss=position.realized_gain_loss,
        currency=position.currency,
        updated_at=position.updated_at,
        exposure=position.exposure,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty stripped text")
    return value


__all__ = ["PositionProjection"]

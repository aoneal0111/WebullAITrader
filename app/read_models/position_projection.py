from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from threading import RLock
from collections.abc import Iterable

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
_LATEST_MARK_CACHE_LIMIT = 256
_PAPER_REPLAY_SOURCE = "paper-execution-replay"


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
        # Market data can legitimately arrive while durable PAPER authority is
        # still being restored. Keep only a bounded, in-memory latest mark so
        # reconciliation can value a position immediately without changing
        # fill/order authority.
        self._latest_marks: dict[str, tuple[datetime, Decimal]] = {}
        self._health = "HEALTHY"
        self._last_reconciliation_source: str | None = None

    @property
    def snapshot(self) -> PositionsReadModelSnapshot:
        with self._lock:
            return self._snapshot

    def memory_metrics(self) -> dict[str, int]:
        with self._lock:
            return {
                "position_count": len(self._snapshot.positions),
                "processed_fill_id_count": len(self._processed_fill_ids),
                "source_sequence_count": len(self._last_sequence_by_source),
            }

    @property
    def health(self) -> str:
        with self._lock:
            return self._health

    @property
    def last_reconciliation_source(self) -> str | None:
        with self._lock:
            return self._last_reconciliation_source

    def mark_degraded(self) -> None:
        """Mark this non-authoritative read model unusable for decisions."""

        with self._lock:
            self._health = "DEGRADED"

    def reconcile_from_paper_orders(
        self,
        orders: Iterable[object],
        *,
        source: str = "paper-order-authority",
    ) -> None:
        """Seed positions from restored authoritative PAPER order state.

        The PAPER order book is the authority for restored exposure.  This
        path intentionally derives only net quantity and average entry; P&L
        remains unavailable because order-book fills do not carry the
        projection's realized-P&L field.
        """

        if not isinstance(source, str) or not source.strip():
            raise ValueError("reconciliation source must be non-empty")
        fills_by_symbol: dict[str, list[tuple[datetime, str, Decimal, Decimal]]] = {}
        for order in orders:
            symbol = getattr(order, "symbol", None)
            request = getattr(order, "request", None)
            fills = getattr(order, "fills", ())
            side = getattr(request, "side", None)
            side_value = getattr(side, "value", side)
            if not isinstance(symbol, str) or side_value not in {"BUY", "SELL"}:
                continue
            for fill in fills:
                quantity = getattr(fill, "quantity", None)
                price = getattr(fill, "price", None)
                timestamp = getattr(fill, "timestamp", None)
                if (
                    not isinstance(quantity, Decimal)
                    or quantity <= ZERO
                    or not isinstance(price, Decimal)
                    or price <= ZERO
                    or not isinstance(timestamp, datetime)
                    or timestamp.tzinfo is None
                ):
                    continue
                fills_by_symbol.setdefault(symbol, []).append(
                    (timestamp, side_value, quantity, price)
                )

        positions: list[PositionReadModel] = []
        for symbol, fills in fills_by_symbol.items():
            quantity = ZERO
            average_cost = ZERO
            updated_at = max(item[0] for item in fills)
            for _timestamp, side, fill_quantity, fill_price in sorted(
                fills, key=lambda item: item[0]
            ):
                if side == "BUY":
                    quantity += fill_quantity
                    average_cost = (
                        (quantity - fill_quantity) * average_cost
                        + fill_quantity * fill_price
                    ) / quantity
                elif fill_quantity <= quantity:
                    quantity -= fill_quantity
                else:
                    # An inconsistent restored sell cannot create a short
                    # projection.  Leave the authority issue visible by
                    # refusing to seed this symbol.
                    quantity = ZERO
                    average_cost = ZERO
                    break
            if quantity > ZERO:
                position = PositionReadModel(
                    account_id=self._account_id,
                    symbol=symbol,
                    asset_type=self._asset_type,
                    quantity=_decimal_text(quantity),
                    average_cost=_decimal_text(average_cost),
                    market_value=None,
                    unrealized_gain_loss=None,
                    realized_gain_loss=None,
                    currency=self._currency,
                    updated_at=updated_at,
                    exposure=None,
                )
                cached = self._latest_marks.get(symbol.upper())
                if cached is not None:
                    mark_at, mark_price = cached
                    market_value, unrealized, exposure = _valuation(
                        quantity=quantity,
                        average_cost=average_cost,
                        mark_price=mark_price,
                    )
                    position = replace(
                        position,
                        market_value=_optional_decimal_text(market_value),
                        unrealized_gain_loss=_optional_decimal_text(unrealized),
                        exposure=_optional_decimal_text(exposure),
                        updated_at=max(updated_at, mark_at),
                    )
                positions.append(position)

        with self._lock:
            self._snapshot = PositionsReadModelSnapshot(
                positions=tuple(sorted(positions, key=lambda item: item.symbol))
            )
            active_symbols = {position.symbol for position in positions}
            self._latest_marks = {
                symbol: value
                for symbol, value in self._latest_marks.items()
                if symbol in active_symbols
            }
            self._last_reconciliation_source = source.strip()
            self._health = "HEALTHY"
            projected = self._snapshot

        self._bus.publish(
            PositionsUpdated(
                occurred_at=(
                    max(
                        (position.updated_at for position in projected.positions),
                        default=datetime.now().astimezone(),
                    )
                ),
                source="paper-authority-position-reconciliation",
                positions=tuple(
                    _to_operations_position(position)
                    for position in projected.positions
                ),
                projection_authority=ProjectionAuthority.PAPER_EXECUTION,
            )
        )

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

            # Durable startup replay is an observational history stream.  It
            # can be an incomplete window (for example, containing a legacy
            # SELL without the earlier BUY), while authoritative PAPER order
            # reconciliation runs separately from the order book.  Do not let
            # that non-authoritative stream mutate or degrade the live
            # position projection.  Other runtime sinks still receive the
            # event through CompositeRuntimeEventSink.
            if (
                event.source == _PAPER_REPLAY_SOURCE
                and event.fill is not None
            ):
                self._last_sequence_by_source[event.source] = event.sequence
                return

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
                self._remember_mark(
                    event.symbol,
                    event.timestamp,
                    event.mark_price,
                )
                projected = _reduce_mark(
                    self._snapshot,
                    symbol=event.symbol,
                    mark_price=event.mark_price,
                    timestamp=event.timestamp,
                )
                occurred_at = event.timestamp
            else:
                return
            self._last_sequence_by_source[event.source] = event.sequence
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

    def _remember_mark(
        self,
        symbol: str,
        timestamp: datetime,
        mark_price: Decimal,
    ) -> None:
        """Retain a bounded latest mark for pre-reconciliation replay."""

        normalized = symbol.strip().upper()
        previous = self._latest_marks.get(normalized)
        if previous is not None and timestamp < previous[0]:
            return
        self._latest_marks[normalized] = (timestamp, mark_price)
        if len(self._latest_marks) > _LATEST_MARK_CACHE_LIMIT:
            oldest = min(
                self._latest_marks.items(),
                key=lambda item: item[1][0],
            )[0]
            self._latest_marks.pop(oldest, None)


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

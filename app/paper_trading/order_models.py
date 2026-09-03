from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.momentum_scanner import AssetClass
from app.paper_trading.fill_models import Fill

ZERO = Decimal("0")


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"


class OrderStatus(StrEnum):
    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OrderTerminalReason(StrEnum):
    """Durable, operator-visible reason for a non-fill terminal transition."""

    DAY_EXPIRED = "DAY_EXPIRED"
    ENTRY_STALE = "ENTRY_STALE"
    STRUCTURAL_STOP_INVALIDATED = "STRUCTURAL_STOP_INVALIDATED"
    OPERATOR_CANCELLED = "OPERATOR_CANCELLED"
    PROTECTIVE_REPLACED = "PROTECTIVE_REPLACED"


TERMINAL_ORDER_STATUSES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    asset_class: AssetClass
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    client_order_id: str | None = None
    strategy_lifecycle_id: str | None = None
    structural_stop_price: Decimal | None = None
    execution_reason: str | None = None
    entry_valid_until: datetime | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("symbol is required")

        if self.quantity <= ZERO:
            raise ValueError("quantity must be positive")

        _validate_order_prices(
            order_type=self.order_type,
            limit_price=self.limit_price,
            stop_price=self.stop_price,
        )

        client_order_id = (
            self.client_order_id.strip()
            if self.client_order_id
            and self.client_order_id.strip()
            else None
        )

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(
            self,
            "client_order_id",
            client_order_id,
        )

        if (
            self.structural_stop_price is not None
            and self.structural_stop_price <= ZERO
        ):
            raise ValueError("structural_stop_price must be positive")
        lifecycle_id = (
            self.strategy_lifecycle_id.strip()
            if self.strategy_lifecycle_id
            and self.strategy_lifecycle_id.strip()
            else None
        )
        object.__setattr__(self, "strategy_lifecycle_id", lifecycle_id)
        execution_reason = (
            self.execution_reason.strip().upper()
            if self.execution_reason and self.execution_reason.strip()
            else None
        )
        object.__setattr__(self, "execution_reason", execution_reason)
        if self.entry_valid_until is not None:
            _require_aware_datetime(
                self.entry_valid_until,
                "entry_valid_until",
            )


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    request: OrderRequest
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    filled_quantity: Decimal = ZERO
    average_fill_price: Decimal | None = None
    rejection_reason: str | None = None
    fills: tuple[Fill, ...] = ()
    terminal_reason: OrderTerminalReason | None = None

    def __post_init__(self) -> None:
        order_id = self.order_id.strip()

        if not order_id:
            raise ValueError("order_id is required")

        _require_aware_datetime(self.created_at, "created_at")
        _require_aware_datetime(self.updated_at, "updated_at")

        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot precede created_at"
            )

        if self.filled_quantity < ZERO:
            raise ValueError(
                "filled_quantity cannot be negative"
            )

        if self.filled_quantity > self.request.quantity:
            raise ValueError(
                "filled_quantity cannot exceed order quantity"
            )

        if (
            self.average_fill_price is not None
            and self.average_fill_price <= ZERO
        ):
            raise ValueError(
                "average_fill_price must be positive"
            )

        if self.filled_quantity > ZERO:
            if self.average_fill_price is None:
                raise ValueError(
                    "average_fill_price is required "
                    "when filled_quantity is positive"
                )
        elif self.average_fill_price is not None:
            raise ValueError(
                "average_fill_price requires a fill"
            )

        if self.fills:
            if any(fill.order_id != order_id for fill in self.fills):
                raise ValueError(
                    "all fills must belong to the order"
                )

            fills_quantity = sum(
                (fill.quantity for fill in self.fills),
                start=ZERO,
            )
            fills_notional = sum(
                (fill.notional for fill in self.fills),
                start=ZERO,
            )

            if fills_quantity != self.filled_quantity:
                raise ValueError(
                    "fills quantity must equal filled_quantity"
                )

            fills_average = fills_notional / fills_quantity
            if fills_average != self.average_fill_price:
                raise ValueError(
                    "fills must match average_fill_price"
                )

        if (
            self.status is OrderStatus.FILLED
            and self.filled_quantity != self.request.quantity
        ):
            raise ValueError(
                "filled orders must have their full quantity"
            )

        if self.status is OrderStatus.PARTIALLY_FILLED:
            if not (
                ZERO
                < self.filled_quantity
                < self.request.quantity
            ):
                raise ValueError(
                    "partially filled orders require a "
                    "partial filled quantity"
                )

        rejection_reason = (
            self.rejection_reason.strip()
            if self.rejection_reason
            and self.rejection_reason.strip()
            else None
        )

        if self.status is OrderStatus.REJECTED:
            if rejection_reason is None:
                raise ValueError(
                    "rejected orders require a reason"
                )

            if self.filled_quantity != ZERO:
                raise ValueError(
                    "rejected orders cannot have fills"
                )
        elif rejection_reason is not None:
            raise ValueError(
                "rejection_reason is only valid "
                "for rejected orders"
            )

        if self.terminal_reason is not None:
            if not isinstance(self.terminal_reason, OrderTerminalReason):
                raise TypeError(
                    "terminal_reason must be an OrderTerminalReason or None"
                )
            if self.status not in {
                OrderStatus.CANCELLED,
                OrderStatus.EXPIRED,
            }:
                raise ValueError(
                    "terminal_reason is only valid for cancelled or expired orders"
                )

        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(
            self,
            "rejection_reason",
            rejection_reason,
        )

    @property
    def remaining_quantity(self) -> Decimal:
        return self.request.quantity - self.filled_quantity

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_ORDER_STATUSES

    @property
    def symbol(self) -> str:
        return self.request.symbol

    @property
    def side(self) -> OrderSide:
        return self.request.side

    @property
    def order_type(self) -> OrderType:
        return self.request.order_type

    @property
    def quantity(self) -> Decimal:
        return self.request.quantity

    @property
    def total_commission(self) -> Decimal:
        return sum(
            (fill.commission for fill in self.fills),
            start=ZERO,
        )

    @property
    def total_slippage(self) -> Decimal:
        return sum(
            (fill.slippage for fill in self.fills),
            start=ZERO,
        )


def _validate_order_prices(
    *,
    order_type: OrderType,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
) -> None:
    if limit_price is not None and limit_price <= ZERO:
        raise ValueError("limit_price must be positive")

    if stop_price is not None and stop_price <= ZERO:
        raise ValueError("stop_price must be positive")

    if order_type is OrderType.MARKET:
        if limit_price is not None or stop_price is not None:
            raise ValueError(
                "market orders cannot specify prices"
            )
        return

    if order_type is OrderType.LIMIT:
        if limit_price is None:
            raise ValueError(
                "limit orders require limit_price"
            )

        if stop_price is not None:
            raise ValueError(
                "limit orders cannot specify stop_price"
            )
        return

    if order_type is OrderType.STOP:
        if stop_price is None:
            raise ValueError(
                "stop orders require stop_price"
            )

        if limit_price is not None:
            raise ValueError(
                "stop orders cannot specify limit_price"
            )
        return

    if order_type is OrderType.STOP_LIMIT:
        if limit_price is None or stop_price is None:
            raise ValueError(
                "stop-limit orders require both "
                "limit_price and stop_price"
            )
        return

    raise ValueError(
        f"unsupported order type: {order_type}"
    )


def _require_aware_datetime(
    value: datetime,
    field_name: str,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            f"{field_name} must be timezone-aware"
        )

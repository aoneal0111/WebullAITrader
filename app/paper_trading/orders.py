from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable
from uuid import uuid4

from app.paper_trading.order_models import (
    OrderRequest,
    OrderStatus,
    PaperOrder,
)

ZERO = Decimal("0")


class OrderValidationError(ValueError):
    """Raised when an order operation is invalid."""


class InvalidOrderTransitionError(RuntimeError):
    """Raised when an order lifecycle transition is invalid."""


def create_order(
    request: OrderRequest,
    *,
    order_id_factory: Callable[[], str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PaperOrder:
    identifier_factory = (
        order_id_factory
        if order_id_factory is not None
        else _new_order_id
    )
    timestamp_factory = (
        clock
        if clock is not None
        else _utc_now
    )

    order_id = str(identifier_factory()).strip()

    if not order_id:
        raise OrderValidationError(
            "order ID factory returned an empty value"
        )

    timestamp = timestamp_factory()
    _validate_transition_time(timestamp)

    return PaperOrder(
        order_id=order_id,
        request=request,
        status=OrderStatus.NEW,
        created_at=timestamp,
        updated_at=timestamp,
    )


def accept_order(
    order: PaperOrder,
    *,
    at: datetime | None = None,
) -> PaperOrder:
    _require_status(
        order,
        allowed=(OrderStatus.NEW,),
        target=OrderStatus.ACCEPTED,
    )

    return replace(
        order,
        status=OrderStatus.ACCEPTED,
        updated_at=_transition_time(order, at),
    )


def reject_order(
    order: PaperOrder,
    reason: str,
    *,
    at: datetime | None = None,
) -> PaperOrder:
    _require_status(
        order,
        allowed=(OrderStatus.NEW,),
        target=OrderStatus.REJECTED,
    )

    normalized_reason = reason.strip()

    if not normalized_reason:
        raise OrderValidationError(
            "rejection reason is required"
        )

    return replace(
        order,
        status=OrderStatus.REJECTED,
        updated_at=_transition_time(order, at),
        rejection_reason=normalized_reason,
    )


def cancel_order(
    order: PaperOrder,
    *,
    at: datetime | None = None,
) -> PaperOrder:
    _require_status(
        order,
        allowed=(
            OrderStatus.NEW,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        ),
        target=OrderStatus.CANCELLED,
    )

    return replace(
        order,
        status=OrderStatus.CANCELLED,
        updated_at=_transition_time(order, at),
    )


def expire_order(
    order: PaperOrder,
    *,
    at: datetime | None = None,
) -> PaperOrder:
    _require_status(
        order,
        allowed=(
            OrderStatus.NEW,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        ),
        target=OrderStatus.EXPIRED,
    )

    return replace(
        order,
        status=OrderStatus.EXPIRED,
        updated_at=_transition_time(order, at),
    )


def apply_fill(
    order: PaperOrder,
    quantity: Decimal,
    price: Decimal,
    *,
    at: datetime | None = None,
) -> PaperOrder:
    _require_status(
        order,
        allowed=(
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        ),
        target=OrderStatus.PARTIALLY_FILLED,
    )

    if quantity <= ZERO:
        raise OrderValidationError(
            "fill quantity must be positive"
        )

    if price <= ZERO:
        raise OrderValidationError(
            "fill price must be positive"
        )

    if quantity > order.remaining_quantity:
        raise OrderValidationError(
            "fill quantity exceeds remaining quantity"
        )

    previous_notional = (
        order.filled_quantity
        * (
            order.average_fill_price
            if order.average_fill_price is not None
            else ZERO
        )
    )

    fill_notional = quantity * price
    new_filled_quantity = (
        order.filled_quantity + quantity
    )
    average_fill_price = (
        previous_notional + fill_notional
    ) / new_filled_quantity

    status = (
        OrderStatus.FILLED
        if new_filled_quantity == order.quantity
        else OrderStatus.PARTIALLY_FILLED
    )

    return replace(
        order,
        status=status,
        updated_at=_transition_time(order, at),
        filled_quantity=new_filled_quantity,
        average_fill_price=average_fill_price,
    )


def _require_status(
    order: PaperOrder,
    *,
    allowed: tuple[OrderStatus, ...],
    target: OrderStatus,
) -> None:
    if order.status not in allowed:
        allowed_values = ", ".join(
            status.value for status in allowed
        )

        raise InvalidOrderTransitionError(
            f"cannot transition order {order.order_id} "
            f"from {order.status.value} to {target.value}; "
            f"allowed source statuses: {allowed_values}"
        )


def _transition_time(
    order: PaperOrder,
    value: datetime | None,
) -> datetime:
    timestamp = value if value is not None else _utc_now()
    _validate_transition_time(timestamp)

    if timestamp < order.updated_at:
        raise OrderValidationError(
            "transition time cannot precede updated_at"
        )

    return timestamp


def _validate_transition_time(
    value: datetime,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OrderValidationError(
            "order timestamps must be timezone-aware"
        )


def _new_order_id() -> str:
    return f"PAPER-{uuid4().hex.upper()}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


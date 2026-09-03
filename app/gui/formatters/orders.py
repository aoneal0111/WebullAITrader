from __future__ import annotations

from decimal import Decimal

from app.gui.formatters.prices import format_price
from app.gui.models import OrdersSnapshot
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot
from app.operations_core.projection_authority import ACTIVE_ORDER_STATUSES

RECENT_TERMINAL_ORDER_LIMIT = 25


def format_orders(snapshot: OrdersReadModelSnapshot) -> OrdersSnapshot:
    """Format authoritative order facts for Mission Control."""

    if not isinstance(snapshot, OrdersReadModelSnapshot):
        raise TypeError("snapshot must be an OrdersReadModelSnapshot")

    active = sorted(
        (
            order for order in snapshot.orders
            if _normalized_status(order.status) in ACTIVE_ORDER_STATUSES
        ),
        key=lambda order: (order.updated_at, order.order_id),
        reverse=True,
    )
    terminal = sorted(
        (
            order for order in snapshot.orders
            if _normalized_status(order.status) not in ACTIVE_ORDER_STATUSES
        ),
        key=lambda order: (order.updated_at, order.order_id),
        reverse=True,
    )[:RECENT_TERMINAL_ORDER_LIMIT]
    visible = (*active, *terminal)
    return OrdersSnapshot(
        rows=tuple(_format_order(order) for order in visible),
        protective_rows=frozenset(
            index for index, order in enumerate(visible)
            if has_explicit_protection_evidence(order)
        ),
    )


def _format_order(order: OrderReadModel) -> tuple[str, ...]:
    return (
        order.symbol,
        order.side,
        _known(order.order_type),
        order.quantity,
        _known(order.filled_quantity),
        _known(order.remaining_quantity),
        _price(order.limit_price),
        _price(order.stop_price),
        _price(order.average_fill_price),
        order.status,
    )


def _known(value: str | None) -> str:
    return "—" if value is None else value


def _price(value: str | None) -> str:
    return "—" if value is None else format_price(Decimal(value))


def has_explicit_protection_evidence(order: OrderReadModel) -> bool:
    """Identify explicit protective STOP provenance without position inference."""
    reason = (order.execution_reason or "").upper()
    return (
        (order.order_type or "").upper() == "STOP"
        and order.stop_price is not None
        and any(token in reason for token in ("STOP", "PROTECT"))
        and order.lifecycle_id is not None
    )


def _normalized_status(status: str) -> str:
    return status.strip().upper().replace(" ", "_")

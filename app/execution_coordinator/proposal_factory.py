from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.order_compliance.models import (
    OrderSide,
    OrderType,
    ProposedOrder,
    TradingSession,
)


class ProposedOrderIntent(Protocol):
    timestamp: datetime
    request_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: object
    limit_price: object
    stop_price: object
    requested_session: TradingSession


def create_proposed_order(
    intent: ProposedOrderIntent,
    *,
    created_timestamp: datetime | None = None,
) -> ProposedOrder:
    """
    Convert an executable order intent into an immutable ProposedOrder.

    The optional timestamp preserves callers that intentionally assign a
    processing or replay timestamp instead of the intent timestamp.
    """
    return ProposedOrder(
        request_id=intent.request_id,
        symbol=intent.symbol,
        side=intent.side,
        order_type=intent.order_type,
        quantity=intent.quantity,
        limit_price=intent.limit_price,
        stop_price=intent.stop_price,
        requested_session=intent.requested_session,
        created_timestamp=(
            intent.timestamp
            if created_timestamp is None
            else created_timestamp
        ),
    )

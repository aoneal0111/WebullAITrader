from __future__ import annotations

from dataclasses import replace
from app.broker_protocol.models import BrokerOrderStatus as LiveOrderStatus
from app.live_execution.models import LocalOrder,LocalPortfolioState

TRANSITIONS = {
    LiveOrderStatus.NEW: frozenset((LiveOrderStatus.SUBMITTED, LiveOrderStatus.REJECTED)),
    LiveOrderStatus.SUBMITTED: frozenset((LiveOrderStatus.ACKNOWLEDGED, LiveOrderStatus.PARTIALLY_FILLED, LiveOrderStatus.FILLED, LiveOrderStatus.REJECTED, LiveOrderStatus.CANCELLED)),
    LiveOrderStatus.ACKNOWLEDGED: frozenset((LiveOrderStatus.PARTIALLY_FILLED, LiveOrderStatus.FILLED, LiveOrderStatus.REJECTED, LiveOrderStatus.CANCELLED)),
    LiveOrderStatus.PARTIALLY_FILLED: frozenset((LiveOrderStatus.PARTIALLY_FILLED, LiveOrderStatus.FILLED, LiveOrderStatus.CANCELLED, LiveOrderStatus.REJECTED)),
    LiveOrderStatus.FILLED: frozenset(), LiveOrderStatus.CANCELLED: frozenset(), LiveOrderStatus.REJECTED: frozenset(),
}


def transition(order: LocalOrder, status: LiveOrderStatus, timestamp, *, broker_order_id=None, fills=None):
    if status is order.status:
        return order
    if status not in TRANSITIONS[order.status]: raise ValueError(f"illegal order transition: {order.status} -> {status}")
    updated_fills = order.fills if fills is None else tuple(sorted(fills, key=lambda item: (item.timestamp, item.fill_id)))
    quantity = sum((item.quantity for item in updated_fills), start=order.filled_quantity * 0)
    if quantity > order.request.quantity: raise ValueError("filled quantity exceeds order quantity")
    if status is LiveOrderStatus.FILLED and quantity != order.request.quantity: raise ValueError("FILLED requires the full quantity")
    if status is LiveOrderStatus.PARTIALLY_FILLED and not 0 < quantity < order.request.quantity: raise ValueError("PARTIALLY_FILLED quantity is invalid")
    return replace(order, status=status, broker_order_id=broker_order_id or order.broker_order_id,
                   filled_quantity=quantity, fills=updated_fills, updated_timestamp=timestamp)


def upsert_order(state: LocalPortfolioState, order: LocalOrder) -> LocalPortfolioState:
    others = tuple(item for item in state.orders if item.request.client_order_id != order.request.client_order_id)
    return replace(state, orders=tuple(sorted((*others, order), key=lambda item: item.request.client_order_id)))

"""Deterministic serializers for existing lifecycle contracts."""
from app.paper_trading.exceptions import PaperTradingSerializationError
from app.paper_trading.fill_models import Fill
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderRequest, PaperOrder


def _require(value: object, expected: type):
    if not isinstance(value, expected):
        raise PaperTradingSerializationError(
            f"value must be {expected.__name__}"
        )
    return value


def serialize_order_book_request(value: OrderRequest) -> dict[str, object]:
    value = _require(value, OrderRequest)
    return {
        "symbol": value.symbol,
        "asset_class": value.asset_class.value,
        "side": value.side.value,
        "order_type": value.order_type.value,
        "quantity": str(value.quantity),
        "time_in_force": value.time_in_force.value,
        "limit_price": (
            str(value.limit_price)
            if value.limit_price is not None
            else None
        ),
        "stop_price": (
            str(value.stop_price)
            if value.stop_price is not None
            else None
        ),
        "client_order_id": value.client_order_id,
    }


def serialize_order_book_fill(value: Fill) -> dict[str, object]:
    value = _require(value, Fill)
    return {
        "fill_id": value.fill_id,
        "order_id": value.order_id,
        "quantity": str(value.quantity),
        "price": str(value.price),
        "timestamp": value.timestamp.isoformat(),
        "commission": str(value.commission),
        "slippage": str(value.slippage),
        "venue": value.venue,
        "liquidity_flag": value.liquidity_flag,
    }


def serialize_order_book_order(value: PaperOrder) -> dict[str, object]:
    value = _require(value, PaperOrder)
    return {
        "order_id": value.order_id,
        "request": serialize_order_book_request(value.request),
        "status": value.status.value,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "filled_quantity": str(value.filled_quantity),
        "average_fill_price": (
            str(value.average_fill_price)
            if value.average_fill_price is not None
            else None
        ),
        "rejection_reason": value.rejection_reason,
        "fills": [serialize_order_book_fill(fill) for fill in value.fills],
    }


def serialize_order_book(value: PaperOrderBook) -> dict[str, object]:
    value = _require(value, PaperOrderBook)
    return {
        "orders": [
            serialize_order_book_order(order)
            for order in value.history()
        ]
    }


__all__ = (
    "serialize_order_book_request",
    "serialize_order_book_fill",
    "serialize_order_book_order",
    "serialize_order_book",
)

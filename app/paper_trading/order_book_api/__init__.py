"""Stable facade for existing Paper Trading order-book lifecycle contracts.

Transition helpers retain their existing optional clock and UUID defaults.
Strict deterministic callers must explicitly supply timestamps and identity
factories; this facade never calls those defaults during import.
"""
from app.paper_trading.fill_models import Fill
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    TimeInForce,
)
from app.paper_trading.orders import (
    accept_order,
    apply_fill,
    cancel_order,
    create_order,
    expire_order,
    reject_order,
)
from app.paper_trading.order_book_api.exceptions import (
    DuplicateOrderError,
    InvalidOrderTransitionError,
    OrderBookError,
    OrderBookSerializationError,
    OrderBookValidationError,
    OrderNotFoundError,
    StaleOrderUpdateError,
)
from app.paper_trading.order_book_api.interfaces import PaperOrderBookInterface
from app.paper_trading.order_book_api.operations import (
    accept,
    cancel,
    create_submission_order,
    expire,
    expire_day_orders,
    record_fill,
    submit,
    update,
)
from app.paper_trading.order_book_api.serializers import (
    serialize_order_book,
    serialize_order_book_fill,
    serialize_order_book_order,
    serialize_order_book_request,
)

OrderBookPaperOrder = PaperOrder
OrderBookFill = Fill
OrderBookOrderRequest = OrderRequest
OrderBookOrderStatus = OrderStatus
OrderBookOrderSide = OrderSide
OrderBookOrderType = OrderType
OrderBookTimeInForce = TimeInForce

__all__ = (
    "PaperOrderBook",
    "PaperOrderBookInterface",
    "OrderBookPaperOrder",
    "OrderBookFill",
    "OrderBookOrderRequest",
    "OrderBookOrderStatus",
    "OrderBookOrderSide",
    "OrderBookOrderType",
    "OrderBookTimeInForce",
    "create_order",
    "accept_order",
    "reject_order",
    "cancel_order",
    "expire_order",
    "apply_fill",
    "serialize_order_book_request",
    "serialize_order_book_fill",
    "serialize_order_book_order",
    "serialize_order_book",
    "OrderBookError",
    "OrderBookValidationError",
    "OrderBookSerializationError",
    "DuplicateOrderError",
    "OrderNotFoundError",
    "StaleOrderUpdateError",
    "InvalidOrderTransitionError",
    "submit",
    "update",
    "cancel",
    "accept",
    "expire",
    "record_fill",
    "expire_day_orders",
    "create_submission_order",
)

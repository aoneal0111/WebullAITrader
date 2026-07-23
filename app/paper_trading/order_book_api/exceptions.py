"""Identity-preserving lifecycle exception exports."""
from app.paper_trading.exceptions import PaperTradingSerializationError
from app.paper_trading.order_book import (
    DuplicateOrderError,
    OrderBookError,
    OrderNotFoundError,
    StaleOrderUpdateError,
)
from app.paper_trading.orders import (
    InvalidOrderTransitionError,
    OrderValidationError,
)

OrderBookValidationError = OrderValidationError
OrderBookSerializationError = PaperTradingSerializationError

__all__ = (
    "OrderBookError",
    "OrderBookValidationError",
    "OrderBookSerializationError",
    "DuplicateOrderError",
    "OrderNotFoundError",
    "StaleOrderUpdateError",
    "InvalidOrderTransitionError",
)

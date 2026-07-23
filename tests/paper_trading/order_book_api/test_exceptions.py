from app.paper_trading.exceptions import PaperTradingSerializationError
from app.paper_trading.order_book import (
    DuplicateOrderError,
    OrderBookError,
    OrderNotFoundError,
    StaleOrderUpdateError,
)
from app.paper_trading.order_book_api import (
    DuplicateOrderError as PublicDuplicateOrderError,
    InvalidOrderTransitionError as PublicInvalidOrderTransitionError,
    OrderBookError as PublicOrderBookError,
    OrderBookSerializationError,
    OrderBookValidationError,
    OrderNotFoundError as PublicOrderNotFoundError,
    StaleOrderUpdateError as PublicStaleOrderUpdateError,
)
from app.paper_trading.orders import (
    InvalidOrderTransitionError,
    OrderValidationError,
)


def test_exception_exports_preserve_existing_identities() -> None:
    assert PublicOrderBookError is OrderBookError
    assert PublicDuplicateOrderError is DuplicateOrderError
    assert PublicOrderNotFoundError is OrderNotFoundError
    assert PublicStaleOrderUpdateError is StaleOrderUpdateError
    assert PublicInvalidOrderTransitionError is InvalidOrderTransitionError
    assert OrderBookValidationError is OrderValidationError
    assert OrderBookSerializationError is PaperTradingSerializationError

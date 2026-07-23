"""Internal immutable command compatibility contracts."""

from types import MappingProxyType
from typing import Mapping

from app.paper_order_book.models import PaperOrderBookRejection
from app.paper_trading.order_book_api import (
    OrderBookFill,
    OrderBookPaperOrder,
    PaperOrderBook,
)

ACCEPT = "accept"
APPLY_FILL = "apply_fill"
CANCEL = "cancel"
EXPIRE = "expire"
EXPIRE_DAY_ORDERS = "expire_day_orders"
REJECT = "reject"
SUBMIT = "submit"
UPDATE = "update"

COMMAND_PAYLOAD_TYPES: Mapping[str, type] = MappingProxyType(
    {
        SUBMIT: OrderBookPaperOrder,
        UPDATE: OrderBookPaperOrder,
        CANCEL: OrderBookPaperOrder,
        ACCEPT: OrderBookPaperOrder,
        REJECT: PaperOrderBookRejection,
        EXPIRE: OrderBookPaperOrder,
        APPLY_FILL: OrderBookFill,
        EXPIRE_DAY_ORDERS: PaperOrderBook,
    }
)
SUPPORTED_COMMAND_TYPES = tuple(COMMAND_PAYLOAD_TYPES)

__all__ = ()

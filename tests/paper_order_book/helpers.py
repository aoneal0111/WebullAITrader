from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.momentum_scanner import AssetClass
from app.paper_order_book import (
    PaperOrderBookCommand,
    PaperOrderBookIdentity,
    PaperOrderBookRequest,
    PaperOrderBookObservation,
)
from app.paper_trading.order_book_api import (
    OrderBookOrderRequest,
    OrderBookOrderSide,
    OrderBookOrderStatus,
    OrderBookOrderType,
    OrderBookPaperOrder,
    OrderBookTimeInForce,
    PaperOrderBook,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def make_lifecycle_request(symbol: str = "AAPL"):
    return OrderBookOrderRequest(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        side=OrderBookOrderSide.BUY,
        order_type=OrderBookOrderType.MARKET,
        quantity=Decimal("10"),
        time_in_force=OrderBookTimeInForce.DAY,
    )


def make_order(order_id: str = "ORDER-1"):
    return OrderBookPaperOrder(
        order_id=order_id,
        request=make_lifecycle_request(),
        status=OrderBookOrderStatus.NEW,
        created_at=NOW,
        updated_at=NOW,
    )


def make_request(*, commands=None, identity=None, snapshot_identity=None):
    identity = identity or PaperOrderBookIdentity("BOOK-1")
    snapshot_identity = snapshot_identity or identity
    book = PaperOrderBook()
    book.submit(make_order())
    snapshot = PaperOrderBookObservation(snapshot_identity, book, NOW)
    if commands is None:
        commands = (
            PaperOrderBookCommand(
                "COMMAND-1",
                "submit",
                make_order("ORDER-2"),
                NOW + timedelta(seconds=1),
            ),
        )
    return PaperOrderBookRequest(
        identity=identity,
        snapshot=snapshot,
        commands=commands,
        requested_at=NOW,
        completed_at=NOW + timedelta(minutes=1),
    )

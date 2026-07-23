from datetime import UTC, datetime
from decimal import Decimal

import app.paper_order_book as api
from app.paper_trading.order_book_api import OrderBookFill

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def test_apply_fill_accepts_existing_lifecycle_fill_payload() -> None:
    fill = OrderBookFill(
        fill_id="FILL-1",
        order_id="ORDER-1",
        quantity=Decimal("1"),
        price=Decimal("100"),
        timestamp=NOW,
    )
    command = api.PaperOrderBookCommand(
        command_id="FILL-COMMAND-1",
        command_type="apply_fill",
        payload=fill,
        occurred_at=NOW,
    )
    request = api.create_request(
        identity=api.PaperOrderBookIdentity("BOOK-1"),
        policy=api.PaperOrderBookPolicy(),
        requested_at=NOW,
        completed_at=NOW,
        commands=(command,),
    )

    assert api.validate_request(request).accepted is True

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.market_data import MarketEvent, MarketEventType, TradePayload


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def market_event(
    sequence: int,
    offset_seconds: int,
    symbol: str = "AAPL",
) -> MarketEvent:
    return MarketEvent(
        sequence,
        NOW + timedelta(seconds=offset_seconds),
        symbol,
        "historical",
        MarketEventType.TRADE,
        TradePayload(
            Decimal("100") + sequence,
            Decimal("10"),
            f"trade-{sequence}",
        ),
    )


@pytest.fixture
def historical_events() -> tuple[MarketEvent, ...]:
    return (
        market_event(1, 0),
        market_event(2, 1),
        market_event(3, 2),
    )

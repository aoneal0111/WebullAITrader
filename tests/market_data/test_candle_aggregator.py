from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.market_data.candle_aggregator import CandleAggregator
from app.market_data.candle_models import TimeFrame
from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    TradePayload,
)


def trade(sequence: int, minute: int, second: int, price: str, size: str) -> MarketEvent:
    return MarketEvent(
        sequence=sequence,
        timestamp=datetime(2026, 7, 28, 14, minute, second, tzinfo=timezone.utc),
        symbol="AAPL",
        source="TEST",
        event_type=MarketEventType.TRADE,
        payload=TradePayload(
            price=Decimal(price),
            size=Decimal(size),
            trade_id=str(sequence),
        ),
    )


def test_aggregator_builds_ohlcv_and_emits_on_rollover() -> None:
    aggregator = CandleAggregator(TimeFrame.ONE_MINUTE, "aapl")

    assert aggregator.on_event(trade(1, 0, 1, "100", "50")) is None
    assert aggregator.on_event(trade(2, 0, 10, "102", "25")) is None
    assert aggregator.on_event(trade(3, 0, 20, "99", "100")) is None
    assert aggregator.on_event(trade(4, 0, 40, "101", "10")) is None

    completed = aggregator.on_event(trade(5, 1, 0, "103", "5"))
    assert completed is not None
    assert completed.open == Decimal("100")
    assert completed.high == Decimal("102")
    assert completed.low == Decimal("99")
    assert completed.close == Decimal("101")
    assert completed.volume == Decimal("185")
    assert completed.trade_count == 4

    current = aggregator.current_candle
    assert current is not None
    assert current.open == Decimal("103")
    assert current.trade_count == 1


def test_aggregator_rejects_other_symbols() -> None:
    aggregator = CandleAggregator(TimeFrame.ONE_MINUTE, "AAPL")
    event = trade(1, 0, 1, "100", "1")
    other = MarketEvent(
        sequence=event.sequence,
        timestamp=event.timestamp,
        symbol="MSFT",
        source=event.source,
        event_type=event.event_type,
        payload=event.payload,
    )
    with pytest.raises(ValueError, match="symbol"):
        aggregator.on_event(other)


def test_aggregator_rejects_non_trade_event() -> None:
    aggregator = CandleAggregator(TimeFrame.ONE_MINUTE)
    event = MarketEvent(
        sequence=1,
        timestamp=datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc),
        symbol="AAPL",
        source="TEST",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal("99"),
            ask=Decimal("101"),
            bid_size=Decimal("1"),
            ask_size=Decimal("1"),
        ),
    )
    with pytest.raises(ValueError, match="TRADE"):
        aggregator.on_event(event)


def test_aggregator_rejects_time_regression() -> None:
    aggregator = CandleAggregator(TimeFrame.ONE_MINUTE)
    aggregator.on_event(trade(1, 1, 0, "100", "1"))
    with pytest.raises(ValueError, match="backward"):
        aggregator.on_event(trade(2, 0, 59, "101", "1"))


def test_flush_returns_and_clears_current_candle() -> None:
    aggregator = CandleAggregator(TimeFrame.ONE_MINUTE)
    aggregator.on_event(trade(1, 0, 1, "100", "1"))
    flushed = aggregator.flush()
    assert flushed is not None
    assert aggregator.current_candle is None
    assert aggregator.flush() is None

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.market_data.candle_models import Candle, CandleSeries, TimeFrame


def candle(open_minute: int = 0) -> Candle:
    open_time = datetime(2026, 7, 28, 14, open_minute, tzinfo=timezone.utc)
    return Candle(
        symbol="aapl",
        interval=TimeFrame.ONE_MINUTE,
        open_time=open_time,
        close_time=open_time + TimeFrame.ONE_MINUTE.duration,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("25"),
        trade_count=3,
    )


def test_candle_normalizes_symbol_and_is_immutable() -> None:
    value = candle()
    assert value.symbol == "AAPL"
    with pytest.raises(AttributeError):
        value.close = Decimal("103")


def test_candle_rejects_invalid_ohlc() -> None:
    with pytest.raises(ValueError, match="high"):
        Candle(
            symbol="AAPL",
            interval=TimeFrame.ONE_MINUTE,
            open_time=datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 28, 14, 1, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("98"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )


def test_series_requires_ordered_matching_candles() -> None:
    first = candle(0)
    second = candle(1)
    series = CandleSeries("aapl", TimeFrame.ONE_MINUTE, (first, second))
    assert series.symbol == "AAPL"
    assert series.candles == (first, second)

    with pytest.raises(ValueError, match="strictly ordered"):
        CandleSeries("AAPL", TimeFrame.ONE_MINUTE, (second, first))

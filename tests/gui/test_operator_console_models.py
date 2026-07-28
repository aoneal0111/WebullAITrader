from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.gui.models import CandleInterval, CandleSeriesModel, ChartMarker, ChartMarkerKind, filter_markers


def test_candle_aggregation_is_deterministic_and_bounded() -> None:
    model = CandleSeriesModel(CandleInterval.FIVE_MINUTES, max_candles=2)
    model.set_context("AAPL", venue="PAPER")
    start = datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc)
    model.add_trade(start, Decimal("10"), Decimal("2"))
    model.add_trade(start + timedelta(minutes=3), Decimal("12"), Decimal("3"))
    model.add_trade(start + timedelta(minutes=5), Decimal("9"), Decimal("1"))
    model.add_trade(start + timedelta(minutes=10), Decimal("11"), Decimal("4"))
    snapshot = model.snapshot()
    assert len(snapshot.candles) == 2
    first = snapshot.candles[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (
        Decimal("9"), Decimal("9"), Decimal("9"), Decimal("9"), Decimal("1")
    )
    assert snapshot.symbol == "AAPL" and snapshot.venue == "PAPER"


def test_equal_timestamp_updates_close_and_volume_and_markers_filter() -> None:
    model = CandleSeriesModel()
    timestamp = datetime(2026, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
    model.add_trade(timestamp, Decimal("10"), Decimal("1"))
    model.add_trade(timestamp, Decimal("11"), Decimal("2"))
    candle = model.snapshot().candles[0]
    assert candle.open == Decimal("10")
    assert candle.close == Decimal("11")
    assert candle.volume == Decimal("3")
    markers = (
        ChartMarker(timestamp, "AAPL", ChartMarkerKind.BUY_FILL),
        ChartMarker(timestamp, "MSFT", ChartMarkerKind.SELL_FILL),
    )
    assert filter_markers(markers, "AAPL") == (markers[0],)

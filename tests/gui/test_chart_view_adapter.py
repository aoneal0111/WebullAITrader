from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.gui.models import CandleInterval, CandleSeriesSnapshot
from app.gui.presenters.chart_presenter import ChartCandle, ChartPresentation
from app.gui.presenters.chart_view_adapter import ChartViewAdapter
from app.market_data.candle_models import TimeFrame


NOW = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)


class RecordingChartTarget:
    def __init__(self) -> None:
        self.snapshots: list[CandleSeriesSnapshot] = []

    def render(self, snapshot: CandleSeriesSnapshot) -> None:
        self.snapshots.append(snapshot)


def make_presentation(
    *,
    interval: TimeFrame = TimeFrame.ONE_MINUTE,
) -> ChartPresentation:
    return ChartPresentation(
        symbol="AAPL",
        interval=interval,
        sequence=11,
        created_at=NOW + timedelta(minutes=1),
        candles=(
            ChartCandle(
                open_time=NOW,
                close_time=NOW + timedelta(minutes=1),
                open=Decimal("100.00"),
                high=Decimal("102.00"),
                low=Decimal("99.00"),
                close=Decimal("101.25"),
                volume=Decimal("1250"),
                trade_count=42,
                is_current=False,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (TimeFrame.ONE_MINUTE, CandleInterval.ONE_MINUTE),
        (TimeFrame.FIVE_MINUTES, CandleInterval.FIVE_MINUTES),
        (TimeFrame.FIFTEEN_MINUTES, CandleInterval.FIFTEEN_MINUTES),
    ],
)
def test_adapter_maps_supported_intervals(
    source: TimeFrame,
    expected: CandleInterval,
) -> None:
    target = RecordingChartTarget()
    adapter = ChartViewAdapter(target)

    adapter.render_chart(make_presentation(interval=source))

    assert len(target.snapshots) == 1
    assert target.snapshots[0].interval is expected


def test_adapter_preserves_symbol_and_ohlcv_values() -> None:
    target = RecordingChartTarget()
    adapter = ChartViewAdapter(target)

    adapter.render_chart(make_presentation())

    assert len(target.snapshots) == 1
    snapshot = target.snapshots[0]
    assert snapshot.symbol == "AAPL"
    assert len(snapshot.candles) == 1

    candle = snapshot.candles[0]
    assert candle.timestamp == NOW
    assert candle.open == Decimal("100.00")
    assert candle.high == Decimal("102.00")
    assert candle.low == Decimal("99.00")
    assert candle.close == Decimal("101.25")
    assert candle.volume == Decimal("1250")


def test_adapter_forwards_exactly_one_snapshot_per_presentation() -> None:
    target = RecordingChartTarget()
    adapter = ChartViewAdapter(target)

    adapter.render_chart(make_presentation())
    adapter.render_chart(make_presentation())

    assert len(target.snapshots) == 2


def test_adapter_rejects_invalid_target_and_presentation() -> None:
    with pytest.raises(TypeError, match="render"):
        ChartViewAdapter(object())  # type: ignore[arg-type]

    adapter = ChartViewAdapter(RecordingChartTarget())
    with pytest.raises(TypeError, match="ChartPresentation"):
        adapter.render_chart(object())  # type: ignore[arg-type]


def test_adapter_rejects_unsupported_interval() -> None:
    target = RecordingChartTarget()
    adapter = ChartViewAdapter(target)

    with pytest.raises(ValueError, match="unsupported chart interval: 30m"):
        adapter.render_chart(
            make_presentation(interval=TimeFrame.THIRTY_MINUTES)
        )

    assert target.snapshots == []

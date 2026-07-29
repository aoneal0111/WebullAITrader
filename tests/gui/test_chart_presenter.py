from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.gui.presenters.chart_presenter import ChartPresentation, ChartPresenter
from app.market_data.candle_models import Candle, TimeFrame
from app.market_data.snapshot_models import CandleSeriesSnapshot
from app.market_data.snapshot_publisher import SnapshotPublisher


NOW = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)


class RecordingChartView:
    def __init__(self) -> None:
        self.presentations: list[ChartPresentation] = []

    def render_chart(self, presentation: ChartPresentation) -> None:
        self.presentations.append(presentation)


def make_candle(open_time: datetime, *, close: str) -> Candle:
    return Candle(
        symbol="AAPL",
        interval=TimeFrame.ONE_MINUTE,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal("100.00"),
        high=Decimal("102.00"),
        low=Decimal("99.00"),
        close=Decimal(close),
        volume=Decimal("1250"),
        trade_count=42,
    )


def make_snapshot() -> CandleSeriesSnapshot:
    return CandleSeriesSnapshot(
        symbol="AAPL",
        interval=TimeFrame.ONE_MINUTE,
        sequence=7,
        created_at=NOW + timedelta(minutes=2),
        completed=(make_candle(NOW, close="101.00"),),
        current=make_candle(NOW + timedelta(minutes=1), close="100.50"),
    )


def test_presenter_subscribes_once_and_unsubscribes_once() -> None:
    publisher = SnapshotPublisher()
    presenter = ChartPresenter(publisher, RecordingChartView())

    assert presenter.is_started is False
    assert presenter.start() is True
    assert presenter.start() is False
    assert presenter.is_started is True
    assert publisher.subscriber_count == 1

    assert presenter.stop() is True
    assert presenter.stop() is False
    assert presenter.is_started is False
    assert publisher.subscriber_count == 0


def test_published_snapshot_is_projected_for_chart_view() -> None:
    publisher = SnapshotPublisher()
    view = RecordingChartView()
    presenter = ChartPresenter(publisher, view)
    snapshot = make_snapshot()
    presenter.start()

    publisher.publish(snapshot)

    assert len(view.presentations) == 1
    presentation = view.presentations[0]
    assert presentation.symbol == "AAPL"
    assert presentation.interval is TimeFrame.ONE_MINUTE
    assert presentation.sequence == 7
    assert presentation.created_at == snapshot.created_at
    assert len(presentation.candles) == 2

    completed, current = presentation.candles
    assert completed.open_time == NOW
    assert completed.close == Decimal("101.00")
    assert completed.is_current is False
    assert current.open_time == NOW + timedelta(minutes=1)
    assert current.close == Decimal("100.50")
    assert current.is_current is True


def test_stopped_presenter_no_longer_renders_publications() -> None:
    publisher = SnapshotPublisher()
    view = RecordingChartView()
    presenter = ChartPresenter(publisher, view)
    presenter.start()
    presenter.stop()

    publisher.publish(make_snapshot())

    assert view.presentations == []


def test_chart_presentation_is_immutable() -> None:
    publisher = SnapshotPublisher()
    view = RecordingChartView()
    presenter = ChartPresenter(publisher, view)
    presenter.on_snapshot(make_snapshot())
    presentation = view.presentations[0]

    with pytest.raises(FrozenInstanceError):
        presentation.sequence = 8  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        presentation.candles[0].close = Decimal("103.00")  # type: ignore[misc]


def test_presenter_rejects_invalid_dependencies_and_snapshots() -> None:
    with pytest.raises(TypeError, match="SnapshotPublisher"):
        ChartPresenter(object(), RecordingChartView())  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="render_chart"):
        ChartPresenter(SnapshotPublisher(), object())  # type: ignore[arg-type]

    presenter = ChartPresenter(SnapshotPublisher(), RecordingChartView())
    with pytest.raises(TypeError, match="CandleSeriesSnapshot"):
        presenter.on_snapshot(object())  # type: ignore[arg-type]

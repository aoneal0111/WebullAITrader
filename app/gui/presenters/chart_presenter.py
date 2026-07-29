from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.market_data.candle_models import Candle, TimeFrame
from app.market_data.snapshot_models import CandleSeriesSnapshot
from app.market_data.snapshot_publisher import SnapshotPublisher


@dataclass(frozen=True, slots=True)
class ChartCandle:
    """Immutable candle values prepared for chart rendering."""

    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int
    is_current: bool


@dataclass(frozen=True, slots=True)
class ChartPresentation:
    """Immutable chart-facing projection of one candle-series snapshot."""

    symbol: str
    interval: TimeFrame
    sequence: int
    created_at: datetime
    candles: tuple[ChartCandle, ...]


class ChartView(Protocol):
    """Rendering boundary implemented by a GUI chart view."""

    def render_chart(self, presentation: ChartPresentation) -> None:
        """Render one immutable chart presentation."""


class ChartPresenter:
    """Adapts market-data snapshots into Qt-independent chart presentations."""

    def __init__(self, publisher: SnapshotPublisher, view: ChartView) -> None:
        if not isinstance(publisher, SnapshotPublisher):
            raise TypeError("publisher must be SnapshotPublisher")
        self._validate_view(view)
        self._publisher = publisher
        self._view = view
        self._started = False

    @property
    def is_started(self) -> bool:
        """Return whether the presenter is currently subscribed."""
        return self._started

    def start(self) -> bool:
        """Subscribe to snapshot publication once."""
        if self._started:
            return False
        added = self._publisher.subscribe(self)
        self._started = added or self._started
        return added

    def stop(self) -> bool:
        """Unsubscribe from snapshot publication when active."""
        if not self._started:
            return False
        removed = self._publisher.unsubscribe(self)
        self._started = not removed
        return removed

    def on_snapshot(self, snapshot: CandleSeriesSnapshot) -> None:
        """Project one snapshot and deliver it to the chart view."""
        if not isinstance(snapshot, CandleSeriesSnapshot):
            raise TypeError("snapshot must be CandleSeriesSnapshot")
        self._view.render_chart(self._project(snapshot))

    @classmethod
    def _project(cls, snapshot: CandleSeriesSnapshot) -> ChartPresentation:
        completed = tuple(cls._project_candle(candle, is_current=False) for candle in snapshot.completed)
        current = (
            (cls._project_candle(snapshot.current, is_current=True),)
            if snapshot.current is not None
            else ()
        )
        return ChartPresentation(
            symbol=snapshot.symbol,
            interval=snapshot.interval,
            sequence=snapshot.sequence,
            created_at=snapshot.created_at,
            candles=completed + current,
        )

    @staticmethod
    def _project_candle(candle: Candle, *, is_current: bool) -> ChartCandle:
        return ChartCandle(
            open_time=candle.open_time,
            close_time=candle.close_time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            trade_count=candle.trade_count,
            is_current=is_current,
        )

    @staticmethod
    def _validate_view(view: ChartView) -> None:
        callback = getattr(view, "render_chart", None)
        if not callable(callback):
            raise TypeError("view must define callable render_chart(presentation)")

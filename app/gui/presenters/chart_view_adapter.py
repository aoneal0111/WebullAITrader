from __future__ import annotations

from typing import Protocol

from app.gui.models import (
    Candle as GuiCandle,
    CandleInterval,
    CandleSeriesSnapshot as GuiCandleSeriesSnapshot,
)
from app.gui.presenters.chart_presenter import ChartPresentation
from app.market_data.candle_models import TimeFrame


class CandleChartTarget(Protocol):
    """Existing chart rendering surface consumed through a narrow boundary."""

    def render(self, snapshot: GuiCandleSeriesSnapshot) -> None:
        """Render one GUI candle-series snapshot."""


class ChartViewAdapter:
    """Convert presenter output into the existing chart widget model."""

    _INTERVALS = {
        TimeFrame.ONE_MINUTE: CandleInterval.ONE_MINUTE,
        TimeFrame.FIVE_MINUTES: CandleInterval.FIVE_MINUTES,
        TimeFrame.FIFTEEN_MINUTES: CandleInterval.FIFTEEN_MINUTES,
    }

    def __init__(self, target: CandleChartTarget) -> None:
        callback = getattr(target, "render", None)
        if not callable(callback):
            raise TypeError("target must define callable render(snapshot)")
        self._target = target

    def render_chart(self, presentation: ChartPresentation) -> None:
        """Adapt one chart presentation and forward it to the chart target."""
        if not isinstance(presentation, ChartPresentation):
            raise TypeError("presentation must be ChartPresentation")

        try:
            interval = self._INTERVALS[presentation.interval]
        except KeyError as error:
            raise ValueError(
                f"unsupported chart interval: {presentation.interval.value}"
            ) from error

        snapshot = GuiCandleSeriesSnapshot(
            symbol=presentation.symbol,
            interval=interval,
            candles=tuple(
                GuiCandle(
                    timestamp=candle.open_time,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                )
                for candle in presentation.candles
            ),
        )
        self._target.render(snapshot)

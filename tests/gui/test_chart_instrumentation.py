from datetime import UTC, datetime, timedelta
from decimal import Decimal

from PySide6.QtWidgets import QApplication

from app.gui.chart_geometry import calculate_chart_geometry
from app.gui.models.chart import ChartCandle, ChartViewSnapshot
from app.gui.widgets.market_workspace import ChartPlaceholder


def candle(minute: int, *, base: str = "100", volume="1000") -> ChartCandle:
    opened = Decimal(base)
    return ChartCandle(
        datetime(2026, 8, 6, 13, 30, tzinfo=UTC) + timedelta(minutes=minute),
        opened,
        opened + Decimal("2"),
        opened - Decimal("1"),
        opened + Decimal("1"),
        None if volume is None else Decimal(volume),
    )


def test_axes_use_visible_prices_and_real_new_york_timestamps() -> None:
    candles = tuple(candle(index * 30, base=str(100 + index)) for index in range(6))
    geometry = calculate_chart_geometry(candles, 720, 360)

    assert geometry.visible_min == Decimal("99")
    assert geometry.visible_max == Decimal("107")
    assert geometry.price_min < geometry.visible_min
    assert geometry.price_max > geometry.visible_max
    assert all(geometry.price_min <= tick.value <= geometry.price_max for tick in geometry.price_ticks)
    assert geometry.time_ticks[0].label == "09:30"
    assert geometry.time_ticks[-1].label == "12:00"
    assert tuple(tick.value for tick in geometry.time_ticks) == tuple(
        candles[tick.candle_index].timestamp for tick in geometry.time_ticks
    )


def test_crosshair_snaps_to_nearest_real_candle() -> None:
    candles = tuple(candle(index * 5) for index in range(4))
    geometry = calculate_chart_geometry(candles, 500, 300)
    target = geometry.candle_centers[2] + 2

    assert geometry.nearest_candle(target) == 2
    assert geometry.nearest_candle(geometry.plot_right + 1) is None


def test_flat_single_and_empty_series_are_safe_and_resize_recalculates() -> None:
    empty = calculate_chart_geometry((), 80, 60)
    single = calculate_chart_geometry((candle(0, base="0.01"),), 180, 100)
    resized = calculate_chart_geometry(tuple(candle(i) for i in range(120)), 900, 420)

    assert empty.price_ticks == ()
    assert single.price_min < single.visible_min < single.price_max
    assert single.candle_width >= 1
    assert resized.candle_centers[-1] < resized.plot_right
    assert resized.plot_right <= 900
    assert len(resized.candle_centers) == 120


def test_ohlc_header_tracks_selected_candle_and_missing_volume() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    candles = (candle(0, base="100"), candle(5, base="103", volume=None))
    widget.render(ChartViewSnapshot(symbol="AAPL", candles=candles))

    assert "O 103.00" in widget._ohlc.text()
    assert "Volume \u2014" in widget._ohlc.text()
    widget._show_candle(candles[0])
    assert "O 100.00" in widget._ohlc.text()
    assert "H 102.00" in widget._ohlc.text()
    assert "Volume 1,000" in widget._ohlc.text()
    assert "2026-08-06 09:30 EDT" in widget._ohlc.toolTip()
    widget.render(ChartViewSnapshot())
    assert widget._ohlc.text().count("\u2014") == 7


def test_instrumentation_is_independent_of_streaming_transport() -> None:
    geometry = calculate_chart_geometry((candle(0), candle(5)), 400, 240)

    assert geometry.price_ticks
    assert geometry.time_ticks

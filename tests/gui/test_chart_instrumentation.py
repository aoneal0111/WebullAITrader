from datetime import UTC, datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import QPoint, QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
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


def test_instrument_header_owns_quote_values_without_duplicate_ohlc() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    candles = (candle(0, base="100"), candle(5, base="103", volume=None))
    widget.render(ChartViewSnapshot(symbol="AAPL", candles=candles))

    assert tuple(widget._quote_labels) == (
        "Open", "High", "Low", "Prev Close",
        "Volume", "Bid", "Ask", "Spread",
    )
    assert not hasattr(widget, "_ohlc")
    assert not hasattr(widget, "_quote_details")
    assert widget._quote_values["Volume"].text() == "--"


def test_instrumentation_is_independent_of_streaming_transport() -> None:
    geometry = calculate_chart_geometry((candle(0), candle(5)), 400, 240)

    assert geometry.price_ticks
    assert geometry.time_ticks


def _wheel(widget, x: float, delta: int) -> None:
    event = QWheelEvent(
        QPointF(x, 120), QPointF(x, 120), QPoint(), QPoint(0, delta),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False,
    )
    QApplication.sendEvent(widget, event)


def test_wheel_zoom_bounds_pan_reset_and_axes_after_viewport_change() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    candles = tuple(candle(index, base=str(100 + index / 10)) for index in range(240))
    widget.resize(900, 420)
    widget.show()
    widget.render(ChartViewSnapshot(symbol="XYZ", candles=candles))
    application.processEvents()
    canvas = widget._canvas

    initial = canvas.visible_range
    _wheel(canvas, canvas._geometry.candle_centers[60], 120)
    zoomed = canvas.visible_range
    assert zoomed[1] - zoomed[0] < initial[1] - initial[0]
    assert canvas._geometry.visible_min == min(item.low for item in canvas._geometry.candles)
    assert canvas._geometry.price_ticks and canvas._geometry.time_ticks

    for _ in range(30):
        _wheel(canvas, 450, 120)
    assert canvas.visible_range[1] - canvas.visible_range[0] == 8
    for _ in range(40):
        _wheel(canvas, 450, -120)
    assert canvas.visible_range == (0, 240)

    canvas._visible_count = 40
    canvas._visible_start = 180
    canvas._recalculate_geometry()
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(450, 160), QPointF(450, 160),
                        Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton,
                        Qt.KeyboardModifier.NoModifier)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(650, 160), QPointF(650, 160),
                       Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton,
                       Qt.KeyboardModifier.NoModifier)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(650, 160), QPointF(650, 160),
                          Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas, press)
    QApplication.sendEvent(canvas, move)
    QApplication.sendEvent(canvas, release)
    assert canvas.visible_range[0] < 180

    widget._auto.click()
    assert canvas.visible_range == (120, 240)
    assert canvas._manual_viewport is False


def test_crosshair_remains_nearest_real_candle_after_zoom() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    candles = tuple(candle(index) for index in range(160))
    widget.resize(800, 360)
    widget.show()
    widget.render(ChartViewSnapshot(symbol="XYZ", candles=candles))
    application.processEvents()
    _wheel(widget._canvas, 400, 120)
    target = widget._canvas._geometry.candle_centers[12]
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(target + 1, 140), QPointF(target + 1, 140),
                        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget._canvas, event)
    assert widget._canvas._selected_index == widget._canvas._geometry.source_offset + 12


def test_quote_header_maps_selected_symbol_direction_and_unknown_fields() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    widget.render(ChartViewSnapshot(
        symbol="XYZ", instrument_name="Example Corp", close=Decimal("103"),
        change=Decimal("2"), change_percent=Decimal("1.98"),
        open=Decimal("100"), high=Decimal("104"), low=Decimal("99"),
        previous_close=Decimal("101"), volume=Decimal("1200"),
        selection_source="operator",
    ))
    application.processEvents()

    assert widget._symbol.text() == "XYZ"
    assert widget._security_name.text() == "Example Corp"
    assert widget._last_price.text() == "103.00"
    assert widget._change.text() == "+2.00   +1.98%"
    assert widget._change.property("tone") == "good"
    assert widget._quote_values["Bid"].text() == "--"
    assert widget._symbol.toolTip() == "Explicit operator inspection"

    widget.render(ChartViewSnapshot(symbol="ABC", change=Decimal("-1")))
    assert widget._symbol.text() == "ABC"
    assert widget._last_price.text() == "--"
    assert widget._change.property("tone") == "danger"


def test_chart_toolbar_has_no_enabled_dead_controls() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    del application

    assert widget._crosshair.isEnabled() and widget._crosshair.isCheckable()
    assert widget._auto.isEnabled()
    assert all(not button.isEnabled() for button in widget._unsupported_controls.values())
    assert all(button.toolTip() for button in widget._unsupported_controls.values())


def test_inspect_control_name_tooltip_and_behavior_are_explicit() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    widget.render(ChartViewSnapshot(symbol="XYZ", candles=(candle(0),)))

    assert widget._crosshair.text() == "Inspect"
    assert all(label in widget._crosshair.toolTip() for label in (
        "timestamp", "Open", "High", "Low", "Close", "Change",
        "Change %", "Volume",
    ))
    widget._crosshair.setChecked(False)
    assert widget._canvas._crosshair_enabled is False
    widget._crosshair.setChecked(True)
    assert widget._canvas._crosshair_enabled is True


def test_fit_chart_name_and_tooltip_describe_latest_auto_fit() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    del application

    assert widget._auto.text() == "Fit Chart"
    assert "latest candles" in widget._auto.toolTip()
    assert "automatically fits the visible price range" in widget._auto.toolTip()


def test_live_stale_and_rest_only_require_authoritative_stream_timestamp() -> None:
    application = QApplication.instance() or QApplication([])
    widget = ChartPlaceholder()
    observed = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
    widget.render(ChartViewSnapshot(
        symbol="XYZ",
        last_stream_update=observed,
        stream_stale_after_seconds=2,
        historical_data_available=True,
    ))

    widget._update_live_status(observed + timedelta(seconds=0.4))
    assert widget._live_status.text() == "\u25cf LIVE"
    assert widget._live_detail.text() == "Updated 0.4s ago"

    widget._update_live_status(observed + timedelta(seconds=2.1))
    assert widget._live_status.text() == "\u25cf STALE"

    widget.render(ChartViewSnapshot(
        symbol="XYZ",
        historical_data_available=True,
    ))
    assert widget._live_status.text() == "REST ONLY"

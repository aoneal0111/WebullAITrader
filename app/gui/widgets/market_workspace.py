from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from PySide6.QtCore import QDateTime, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.design.tokens import Colors, Dimensions
from app.gui.chart_geometry import NEW_YORK, calculate_chart_geometry
from app.gui.models import (
    AIThinkingSnapshot,
    AtlasActivitySnapshot,
    ChartViewSnapshot,
    HealthDashboardSnapshot,
    MissionStatusSnapshot,
    WatchlistSnapshot,
)
from app.gui.formatters.prices import format_price
from app.gui.widgets.atlas_activity_panel import AtlasActivityPanel
from app.gui.widgets.ai_thinking_panel import AIThinkingPanel
from app.gui.widgets.data_table import StyledDataTable
from app.gui.widgets.infrastructure_strip import InfrastructureStrip
from app.gui.widgets.mission_status_panel import MissionStatusPanel
from app.gui.widgets.panel import SectionPanel
from app.gui.widgets.trade_intelligence_panel import TradeIntelligencePanel
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.portfolio_summary_strip import PortfolioSummaryStrip
from app.gui.widgets.workstation_panels import MarketOverviewPanel, RuntimeControlsPanel


class ChartView(Protocol):
    def render(self, snapshot: ChartViewSnapshot) -> None: ...


class EmptyChartCanvas(QFrame):
    """Honest chart empty state with terminal-style grid treatment."""

    candle_selected = Signal(object)
    viewport_changed = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("chartCanvas")
        self.setMinimumHeight(Dimensions.CHART_MIN_HEIGHT)
        self._message = "No market series available."
        self._candles = ()
        self._symbol = "--"
        self._geometry = calculate_chart_geometry((), self.width(), self.height())
        self._selected_index = None
        self._pointer_price = None
        self._render_signature = None
        self._visible_count = 120
        self._visible_start = 0
        self._minimum_visible_bars = 8
        self._manual_viewport = False
        self._pan_anchor_x: float | None = None
        self._pan_anchor_start = 0
        self._crosshair_enabled = True
        self.setMouseTracking(True)

    def set_model(self, snapshot: ChartViewSnapshot) -> None:
        prior_symbol = self._symbol
        prior_length = len(self._candles)
        self._candles = snapshot.candles
        self._symbol = snapshot.symbol
        self._message = snapshot.message
        self._selected_index = None
        self._pointer_price = None
        if prior_symbol != self._symbol or not self._manual_viewport:
            self.reset_view()
        else:
            self._visible_count = min(self._visible_count, max(1, len(self._candles)))
            if self._visible_start + self._visible_count >= prior_length:
                self._visible_start = max(0, len(self._candles) - self._visible_count)
            self._recalculate_geometry()
        self.candle_selected.emit(None)
        self.update()

    def set_message(self, message: str) -> None:
        self._message = message
        self._candles = ()
        self._selected_index = None
        self._pointer_price = None
        self._recalculate_geometry()
        self.candle_selected.emit(None)
        self.update()

    @property
    def visible_range(self) -> tuple[int, int]:
        return self._visible_start, self._visible_start + len(self._geometry.candles)

    def reset_view(self) -> None:
        self._manual_viewport = False
        self._visible_count = min(120, len(self._candles)) if self._candles else 0
        self._visible_start = max(0, len(self._candles) - self._visible_count)
        self._selected_index = None
        self._pointer_price = None
        self._recalculate_geometry()
        self.viewport_changed.emit(*self.visible_range)
        self.update()

    def set_crosshair_enabled(self, enabled: bool) -> None:
        self._crosshair_enabled = bool(enabled)
        if not enabled:
            self._selected_index = None
            self._pointer_price = None
            self.candle_selected.emit(None)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(Colors.BACKGROUND))
        if self._candles:
            self._paint_candles(painter)
            return
        painter.setPen(QPen(QColor(Colors.CHART_GRID), 1))
        step_x = max(56, self.width() // 12)
        step_y = max(44, self.height() // 8)
        for x in range(0, self.width(), step_x):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step_y):
            painter.drawLine(0, y, self.width(), y)
        center_x = self.width() / 2
        center_y = self.height() / 2 - 12
        lowered = self._message.lower()
        if self._symbol == "--" and "scanning" in lowered:
            title = "Atlas is scanning"
            detail = "Candidates: 0"
        elif self._symbol == "--":
            title = "Chart idle"
            detail = self._message
        elif "select" in lowered:
            title, detail = "Symbol not selected", "Select a symbol to initialize the chart"
        elif "subscription" in lowered:
            title, detail = "Waiting for subscription", self._message
        elif "entitlement" in lowered:
            title, detail = "Entitlement required", self._message
        else:
            title, detail = "Market data unavailable", self._message
        title_font = painter.font()
        title_font.setPixelSize(16)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(Colors.TEXT))
        painter.drawText(
            QRectF(0, center_y, self.width(), 24),
            Qt.AlignmentFlag.AlignHCenter,
            title,
        )
        detail_font = painter.font()
        detail_font.setPixelSize(12)
        detail_font.setBold(False)
        painter.setFont(detail_font)
        painter.setPen(QColor(Colors.TEXT_MUTED))
        painter.drawText(
            QRectF(40, center_y + 28, self.width() - 80, 36),
            Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
            detail,
        )

    def _paint_candles(self, painter: QPainter) -> None:
        geometry = self._geometry
        candles = geometry.candles
        painter.setPen(QPen(QColor(Colors.CHART_GRID), 1))
        for tick in geometry.price_ticks:
            painter.drawLine(int(geometry.plot_left), int(tick.position), int(geometry.plot_right), int(tick.position))
        for tick in geometry.time_ticks:
            painter.drawLine(int(tick.position), int(geometry.plot_top), int(tick.position), int(geometry.plot_bottom))
        painter.setPen(QColor(Colors.TEXT_MUTED))
        label_font = painter.font()
        label_font.setPixelSize(10)
        painter.setFont(label_font)
        for tick in geometry.price_ticks:
            painter.drawText(
                QRectF(geometry.plot_right + 5, tick.position - 9, max(1, self.width() - geometry.plot_right - 7), 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                tick.label,
            )
        for tick in geometry.time_ticks:
            label_left = min(
                max(geometry.plot_left, tick.position - 39),
                max(geometry.plot_left, geometry.plot_right - 78),
            )
            painter.drawText(
                QRectF(label_left, geometry.plot_bottom + 4, min(78, geometry.plot_right - label_left), 28),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                tick.label,
            )

        painter.save()
        painter.setClipRect(QRectF(
            geometry.plot_left, geometry.plot_top,
            geometry.plot_right - geometry.plot_left,
            geometry.plot_bottom - geometry.plot_top,
        ))
        for x, candle in zip(geometry.candle_centers, candles):
            rising = candle.close >= candle.open
            color = QColor(Colors.SUCCESS if rising else Colors.DANGER)
            painter.setPen(QPen(color, 1))
            painter.drawLine(int(x), int(geometry.y_for_price(candle.high)), int(x), int(geometry.y_for_price(candle.low)))
            opened, closed = geometry.y_for_price(candle.open), geometry.y_for_price(candle.close)
            body_top = min(opened, closed)
            body_height = max(1.0, abs(opened - closed))
            painter.fillRect(
                QRectF(x - geometry.candle_width / 2, body_top, geometry.candle_width, body_height),
                color,
            )
        if self._selected_index is not None:
            local = self._selected_index - geometry.source_offset
            if 0 <= local < len(candles):
                x = geometry.candle_centers[local]
                candle = candles[local]
                global_index = geometry.source_offset + local
                previous = (
                    self._candles[global_index - 1].close
                    if global_index > 0 else candle.open
                )
                change = candle.close - previous
                percent = change / previous * 100 if previous else None
                y = geometry.y_for_price(self._pointer_price or candle.close)
                crosshair = QPen(QColor(Colors.TEXT_MUTED), 1, Qt.PenStyle.DashLine)
                painter.setPen(crosshair)
                painter.drawLine(int(x), int(geometry.plot_top), int(x), int(geometry.plot_bottom))
                painter.drawLine(int(geometry.plot_left), int(y), int(geometry.plot_right), int(y))
        painter.restore()
        if self._selected_index is not None:
            local = self._selected_index - geometry.source_offset
            if 0 <= local < len(candles):
                candle = candles[local]
                x = geometry.candle_centers[local]
                global_index = geometry.source_offset + local
                previous = self._candles[global_index - 1].close if global_index > 0 else candle.open
                change = candle.close - previous
                percent = change / previous * 100 if previous else None
                box_width, box_height = 190.0, 134.0
                box_x = x + 12 if x + 12 + box_width < geometry.plot_right else x - box_width - 12
                box_y = geometry.plot_top + 8
                box = QRectF(max(geometry.plot_left + 2, box_x), box_y, box_width, box_height)
                painter.fillRect(box, QColor(Colors.SURFACE))
                painter.setPen(QPen(QColor(Colors.CHART_GRID), 1))
                painter.drawRect(box)
                tooltip_volume = "\u2014" if candle.volume is None else f"{candle.volume:,.0f}"
                change_line = (
                    f"Change {'+' if change > 0 else ''}{format_price(change)}   Change % {percent:+.2f}%"
                    if percent is not None else "Change --   Change % --"
                )
                tooltip = (
                    f"{candle.timestamp.astimezone(NEW_YORK):%Y-%m-%d %H:%M %Z}\n"
                    f"Open {format_price(candle.open)}   High {format_price(candle.high)}\n"
                    f"Low {format_price(candle.low)}   Close {format_price(candle.close)}\n"
                    f"{change_line}\n"
                    f"Volume {tooltip_volume}"
                )
                painter.setPen(QColor(Colors.TEXT))
                painter.drawText(
                    box.adjusted(8, 7, -8, -7),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    tooltip,
                )
        self._log_render("success")

    def _recalculate_geometry(self) -> None:
        self._geometry = calculate_chart_geometry(
            self._candles,
            self.width(),
            self.height(),
            visible_start=self._visible_start,
            visible_count=max(1, self._visible_count),
        )
        self._log_render("success" if self._candles else "skipped:no_bars")

    def _log_render(self, status: str) -> None:
        geometry = self._geometry
        signature = (self._symbol, len(self._candles), self.width(), self.height(), self._selected_index, status)
        if signature == self._render_signature:
            return
        self._render_signature = signature
        logging.getLogger("atlas.gui.chart").info(
            "operation=chart_render symbol=%s bars=%d earliest=%s latest=%s visible_min=%s visible_max=%s price_ticks=%d time_ticks=%d selected_index=%s status=%s",
            self._symbol,
            len(self._candles),
            self._candles[0].timestamp.isoformat() if self._candles else "--",
            self._candles[-1].timestamp.isoformat() if self._candles else "--",
            geometry.visible_min if geometry.visible_min is not None else "--",
            geometry.visible_max if geometry.visible_max is not None else "--",
            len(geometry.price_ticks), len(geometry.time_ticks),
            self._selected_index if self._selected_index is not None else "--",
            status,
        )

    def mouseMoveEvent(self, event) -> None:
        if self._pan_anchor_x is not None:
            step = max(1.0, (
                self._geometry.plot_right - self._geometry.plot_left
            ) / max(1, len(self._geometry.candles)))
            delta = round((self._pan_anchor_x - event.position().x()) / step)
            maximum_start = max(0, len(self._candles) - self._visible_count)
            start = min(max(0, self._pan_anchor_start + delta), maximum_start)
            if start != self._visible_start:
                self._visible_start = start
                self._manual_viewport = True
                self._recalculate_geometry()
                self.viewport_changed.emit(*self.visible_range)
                self.update()
            return
        if not self._crosshair_enabled:
            return super().mouseMoveEvent(event)
        index = self._geometry.nearest_candle(event.position().x())
        inside = (
            index is not None
            and self._geometry.plot_top <= event.position().y() <= self._geometry.plot_bottom
        )
        selected = index if inside else None
        self._pointer_price = self._geometry.price_for_y(event.position().y()) if inside else None
        if selected != self._selected_index:
            self._selected_index = selected
            self.candle_selected.emit(None if selected is None else self._candles[selected])
            self._log_render("success")
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_anchor_x = event.position().x()
            self._pan_anchor_start = self._visible_start
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_anchor_x is not None:
            self._pan_anchor_x = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if not self._candles or not self._geometry.candles:
            return super().wheelEvent(event)
        direction = event.angleDelta().y()
        if direction == 0:
            return
        old_count = len(self._geometry.candles)
        maximum = len(self._candles)
        minimum = min(self._minimum_visible_bars, maximum)
        factor = 0.8 if direction > 0 else 1.25
        new_count = min(maximum, max(minimum, round(old_count * factor)))
        if new_count == old_count:
            event.accept()
            return
        plot_width = max(1.0, self._geometry.plot_right - self._geometry.plot_left)
        ratio = min(1.0, max(0.0, (event.position().x() - self._geometry.plot_left) / plot_width))
        anchor = self._visible_start + ratio * max(0, old_count - 1)
        new_start = round(anchor - ratio * max(0, new_count - 1))
        self._visible_count = new_count
        self._visible_start = min(max(0, new_start), max(0, maximum - new_count))
        self._manual_viewport = True
        self._recalculate_geometry()
        self.viewport_changed.emit(*self.visible_range)
        self.update()
        event.accept()

    def leaveEvent(self, event) -> None:
        self._selected_index = None
        self._pointer_price = None
        self.candle_selected.emit(None)
        self._log_render("success")
        self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        self._recalculate_geometry()
        super().resizeEvent(event)


class ChartPlaceholder(QWidget):
    """Polished placeholder behind the future chart-view boundary."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        self._symbol_selector = QComboBox()
        self._symbol_selector.setMinimumWidth(82)
        self._symbol_selector.setToolTip("Chart symbol")
        self._symbol = QLabel("--")
        self._symbol.setObjectName("sectionTitle")
        self._symbol.setToolTip("Selected security symbol")
        self._security_name = QLabel("Security name unavailable")
        self._security_name.setObjectName("muted")
        self._timeframe = QComboBox()
        self._timeframe.addItems(("1m", "5m", "15m", "1H", "1D"))
        self._timeframe.setCurrentText("1D")
        self._crosshair = QPushButton("Inspect")
        self._crosshair.setObjectName("ghostButton")
        self._crosshair.setCheckable(True)
        self._crosshair.setChecked(True)
        self._crosshair.setToolTip(
            "Inspect: shows timestamp, Open, High, Low, Close, Change, "
            "Change %, and Volume for the candle under the pointer."
        )
        self._auto = QPushButton("Fit Chart")
        self._auto.setObjectName("ghostButton")
        self._auto.setToolTip(
            "Fit Chart: returns to the latest candles and automatically fits "
            "the visible price range."
        )
        self._unsupported_controls = {}
        for name in ("Indicators", "Compare"):
            button = QPushButton(name, self)
            button.setObjectName("ghostButton")
            button.setEnabled(False)
            button.setToolTip(f"{name} is not available in the current chart engine")
            self._unsupported_controls[name] = button
        toolbar.addWidget(self._symbol_selector)
        toolbar.addStretch()
        toolbar.addWidget(self._timeframe)
        toolbar.addWidget(self._crosshair)
        toolbar.addWidget(self._auto)
        toolbar.addWidget(self._unsupported_controls["Indicators"])
        toolbar.addWidget(self._unsupported_controls["Compare"])
        layout.addLayout(toolbar)

        instrument_row = QHBoxLayout()
        instrument_row.addWidget(self._symbol)
        instrument_row.addWidget(self._security_name)
        instrument_row.addStretch()
        self._live_status = QLabel("IDLE")
        self._live_status.setObjectName("monoValue")
        self._live_detail = QLabel("")
        self._live_detail.setObjectName("muted")
        instrument_row.addWidget(self._live_status)
        instrument_row.addWidget(self._live_detail)
        layout.addLayout(instrument_row)

        quote_row = QHBoxLayout()
        self._last_price = QLabel("--")
        self._last_price.setObjectName("quotePrice")
        self._change = QLabel("--   --")
        self._change.setObjectName("monoValue")
        quote_row.addWidget(self._last_price)
        quote_row.addWidget(self._change)
        quote_row.addStretch()
        layout.addLayout(quote_row)

        quote_grid = QGridLayout()
        quote_grid.setContentsMargins(0, 0, 0, 0)
        quote_grid.setHorizontalSpacing(18)
        quote_grid.setVerticalSpacing(3)
        self._quote_labels: dict[str, QLabel] = {}
        self._quote_values: dict[str, QLabel] = {}
        self._quote_metrics: list[QWidget] = []
        self._quote_grid = quote_grid
        self._quote_columns = 0
        for index, label in enumerate((
            "Open", "High", "Low", "Prev Close",
            "Volume", "Bid", "Ask", "Spread",
        )):
            metric = QWidget()
            metric_layout = QVBoxLayout(metric)
            metric_layout.setContentsMargins(0, 0, 0, 0)
            metric_layout.setSpacing(1)
            name = QLabel(label)
            name.setObjectName("muted")
            value = QLabel("--")
            value.setObjectName("monoValue")
            metric_layout.addWidget(name)
            metric_layout.addWidget(value)
            quote_grid.addWidget(metric, index // 4, index % 4)
            self._quote_metrics.append(metric)
            self._quote_labels[label] = name
            self._quote_values[label] = value
        layout.addLayout(quote_grid)
        self._canvas = EmptyChartCanvas()
        self._chart_snapshot = ChartViewSnapshot()
        self._crosshair.toggled.connect(self._canvas.set_crosshair_enabled)
        self._auto.clicked.connect(self._canvas.reset_view)
        layout.addWidget(self._canvas, 1)
        footer = QHBoxLayout()
        self._range_buttons = []
        interaction_hint = QLabel("Wheel: zoom   Middle-drag: pan")
        interaction_hint.setObjectName("muted")
        footer.addWidget(interaction_hint)
        footer.addStretch()
        self._market_time = QLabel()
        self._market_time.setObjectName("muted")
        footer.addWidget(self._market_time)
        for label in ("% scale", "Log scale"):
            button = QPushButton(label)
            button.setObjectName("ghostButton")
            button.setEnabled(False)
            button.setToolTip(f"{label} is intentionally unavailable")
            footer.addWidget(button)
        layout.addLayout(footer)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_time)
        self._clock.start(1000)
        self._update_time()

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        # Keep the quote header dense. Reflow metrics instead of allowing the
        # header to consume the chart at smaller widths.
        columns = 2 if width < 520 else 4 if width < 900 else 8
        if columns != self._quote_columns:
            self._quote_columns = columns
            for index, metric in enumerate(self._quote_metrics):
                self._quote_grid.addWidget(metric, index // columns, index % columns)
        # Secondary chart controls are useful on wide screens but should not
        # steal readable chart width in compact layouts.
        show_secondary = width >= 1050
        for button in self._unsupported_controls.values():
            button.setVisible(show_secondary)
        self._security_name.setVisible(width >= 700)
        self._live_detail.setVisible(width >= 760)
        super().resizeEvent(event)

    def select_symbol(self, symbol: str) -> None:
        self._symbol_selector.blockSignals(True)
        if self._symbol_selector.findText(symbol) < 0:
            self._symbol_selector.addItem(symbol)
        self._symbol_selector.setCurrentText(symbol)
        self._symbol_selector.setEnabled(True)
        self._symbol_selector.blockSignals(False)

    def set_symbols(
        self,
        symbols: tuple[str, ...],
        selected_symbol: str | None = None,
    ) -> None:
        current = self._symbol_selector.currentText()
        self._symbol_selector.blockSignals(True)
        self._symbol_selector.clear()
        self._symbol_selector.addItem("No active symbol")
        self._symbol_selector.addItems(symbols)
        if selected_symbol in symbols:
            self._symbol_selector.setCurrentText(selected_symbol)
        elif current in symbols:
            self._symbol_selector.setCurrentText(current)
        else:
            self._symbol_selector.setCurrentIndex(0)
        self._symbol_selector.setEnabled(bool(symbols))
        self._symbol_selector.blockSignals(False)

    def _update_time(self) -> None:
        self._market_time.setText(
            QDateTime.currentDateTime().toString("MMM d  hh:mm:ss AP")
        )
        self._update_live_status()

    def render(self, snapshot: ChartViewSnapshot) -> None:
        self._chart_snapshot = snapshot
        if snapshot.symbol != "--":
            self.select_symbol(snapshot.symbol)
        self._symbol.setText(snapshot.symbol)
        self._security_name.setText(
            snapshot.instrument_name or "--"
        )
        self._symbol.setToolTip({
            "operator": "Explicit operator inspection",
            "operator inspection": "Explicit operator inspection",
            "atlas candidate": "Active Atlas candidate or focus",
            "active position": "Active position",
            "working order": "Active working order",
        }.get(snapshot.selection_source, "No active instrument"))
        self._timeframe.setCurrentText(snapshot.timeframe)
        self._canvas.set_model(snapshot)
        self._render_quote(snapshot)
        self._update_live_status()

    def _render_quote(self, snapshot: ChartViewSnapshot) -> None:
        self._last_price.setText(_decimal_display(snapshot.close))
        self._change.setText(
            f"{_signed_display(snapshot.change)}   "
            f"{_signed_display(snapshot.change_percent, suffix='%')}"
        )
        tone = (
            "good" if snapshot.change is not None and snapshot.change > 0
            else "danger" if snapshot.change is not None and snapshot.change < 0
            else "neutral"
        )
        for label in (self._last_price, self._change):
            label.setProperty("tone", tone)
            label.style().unpolish(label)
            label.style().polish(label)
        spread = (
            snapshot.ask - snapshot.bid
            if snapshot.ask is not None and snapshot.bid is not None
            else None
        )
        values = {
            "Open": _decimal_display(snapshot.open),
            "High": _decimal_display(snapshot.high),
            "Low": _decimal_display(snapshot.low),
            "Prev Close": _decimal_display(snapshot.previous_close),
            "Volume": _decimal_display(snapshot.volume, precision=0),
            "Bid": _decimal_display(snapshot.bid),
            "Ask": _decimal_display(snapshot.ask),
            "Spread": _decimal_display(spread),
        }
        for label, value in values.items():
            self._quote_values[label].setText(value)

    def _update_live_status(self, now: datetime | None = None) -> None:
        status, detail, tone = _live_data_status(
            self._chart_snapshot,
            now or datetime.now(UTC),
        )
        self._live_status.setText(status)
        self._live_detail.setText(detail)
        self._live_status.setProperty("tone", tone)
        self._live_status.style().unpolish(self._live_status)
        self._live_status.style().polish(self._live_status)


def _decimal_display(value, *, precision: int = 2) -> str:
    return "--" if value is None else f"{value:,.{precision}f}"


def _signed_display(value, *, suffix: str = "") -> str:
    return "--" if value is None else f"{value:+,.2f}{suffix}"


def _live_data_status(
    snapshot: ChartViewSnapshot,
    now: datetime,
) -> tuple[str, str, str]:
    if snapshot.symbol == "--":
        return "IDLE", "", "neutral"
    updated = snapshot.last_stream_update
    if updated is not None:
        age = max(0.0, (now.astimezone(UTC) - updated.astimezone(UTC)).total_seconds())
        detail = f"Updated {age:.1f}s ago"
        if age <= snapshot.stream_stale_after_seconds:
            return "\u25cf LIVE", detail, "good"
        return "\u25cf STALE", detail, "warn"
    if snapshot.historical_data_available:
        return "REST ONLY", "Streaming unavailable", "warn"
    return "NO DATA", "Awaiting authoritative market data", "neutral"


class CompactWatchlistPanel(QWidget):
    candidate_selected = Signal(str)
    focus_mode_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._last_render_fingerprint = None
        self._render_count = 0
        self._visible_rows = ()
        self.setMinimumWidth(Dimensions.WATCHLIST_MIN_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        status_row = QHBoxLayout()
        scanner_title = QLabel("ATLAS SCANNER")
        scanner_title.setObjectName("sectionTitle")
        self._scanner_status = QLabel("Unknown")
        self._scanner_status.setObjectName("statusPill")
        self._candidate_count = QLabel("Candidates: 0")
        self._candidate_count.setObjectName("monoValue")
        status_row.addWidget(scanner_title)
        status_row.addWidget(self._scanner_status)
        status_row.addWidget(self._candidate_count)
        status_row.addStretch(1)
        self._mode_selector = QComboBox()
        self._mode_selector.addItems(("CURRENT ATLAS", "WARRIOR PAPER"))
        self._mode_selector.currentTextChanged.connect(self.focus_mode_changed.emit)
        self._mode_selector.hide()
        layout.addLayout(status_row)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        self._active_view_filter = "All"
        self._view_buttons: dict[str, QPushButton] = {}
        for label in ("All", "Qualifying", "Near Miss", "Watching"):
            button = QPushButton(label)
            button.setObjectName("scannerFilter")
            button.setCheckable(True)
            button.setChecked(label == "All")
            button.setEnabled(True)
            button.clicked.connect(
                lambda _checked=False, view=label:
                self._set_view_filter(view)
            )
            controls.addWidget(button)
            self._view_buttons[label] = button
        controls.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search symbols\u2026")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self._apply_search)
        self.search.hide()
        layout.addLayout(controls)
        self._paper_summary = QLabel()
        self._paper_summary.setObjectName("monoValue")
        self._paper_funnel = QLabel()
        self._paper_funnel.setObjectName("muted")
        self._paper_research = QLabel()
        self._paper_research.setObjectName("muted")
        for label in (self._paper_summary, self._paper_funnel, self._paper_research):
            label.setVisible(False)
            layout.addWidget(label)
        self._columns = (
            "Rank", "Symbol", "Price", "Chg %", "RVOL", "Score", "Setup",
            "Status", "Freshness",
        )
        self._table = StyledDataTable(self._columns)
        self._table.set_empty_state(
            "Atlas is scanning — no qualifying opportunities yet.",
            "Please wait while we analyze the market.\nOpportunities will appear here.",
            icon="",
        )
        self._configure_columns(self._columns)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._table, 1)
        self._table.cellClicked.connect(self._select_row)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self._source_snapshot = snapshot
        # Scanner state may be published frequently during live markets.
        # Rebuilding the QTableWidget is expensive: clearSelection(),
        # setRowCount(), item construction, header sizing and selectRow()
        # all create Qt model/layout/paint work.  WatchlistSnapshot is an
        # immutable presentation value, so an identical snapshot requires
        # no widget mutation.
        render_fingerprint = (
            snapshot,
            self._mode_selector.currentText(),
            self._active_view_filter,
            self.search.text().strip().upper(),
        )
        if render_fingerprint == self._last_render_fingerprint:
            return
        self._last_render_fingerprint = render_fingerprint
        self._render_count += 1
        status = snapshot.scanner_status.replace("_", " ").title()
        prefix = "Warrior Capture" if self._mode_selector.currentText() == "WARRIOR PAPER" else "Atlas Scanner"
        self._scanner_status.setText(f"{prefix}: {status}")
        self._candidate_count.setText(f"Candidates: {snapshot.candidate_count}")
        empty_title = snapshot.empty_title.rstrip(".")
        empty_detail = " ".join(snapshot.empty_detail.split())
        if empty_title == "Atlas is scanning":
            empty_title = "Atlas is scanning — no qualifying opportunities yet."
            empty_detail = (
                "Please wait while we analyze the market.\n"
                "Opportunities will appear here."
            )
        self._table.set_empty_state(empty_title, empty_detail, icon="")
        self._table.clearSelection()
        query = self.search.text().strip().upper()
        classification = (
            self._active_view_filter.upper()
            if self._active_view_filter != "All"
            else None
        )
        visible_rows = tuple(
            row
            for row in snapshot.rows
            if (
                (not query or query in row.symbol.upper())
                and (
                    classification is None
                    or row.classification == classification
                )
            )
        )
        self._visible_rows = visible_rows
        self._table.setRowCount(len(visible_rows))
        for row_index, row in enumerate(visible_rows):
            state = (
                f"{row.market_status} \u00b7 {row.stale}"
                if row.market_status != "--"
                else row.stale
            )
            status = _first_watchlist_value(
                row.strategy_status,
                row.classification,
                row.setup_state,
                row.freshness,
            )
            values = (
                row.rank,
                f"\u25cf {row.symbol}" if row.selected else row.symbol,
                row.latest_price,
                row.change_percent,
                row.relative_volume,
                row.score,
                row.setup,
                status,
                row.freshness,
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index in (0, 2, 3, 4, 5, 8):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                color = _watchlist_color(self._columns[column_index], value)
                if color is not None:
                    item.setForeground(QBrush(QColor(color)))
                item.setToolTip(state)
                if row.explanations != "--":
                    item.setToolTip(row.explanations)
                self._table.setItem(
                    row_index,
                    column_index,
                    item,
                )
            if row.selected:
                self._table.selectRow(row_index)

    def _configure_columns(self, columns: tuple[str, ...]) -> None:
        """Install stable, user-resizable widths without per-tick autosizing."""
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        widths = {
            "Rank": 38, "Symbol": 54, "Price": 52, "Last": 54,
            "Chg %": 48, "RVOL": 45, "Rel Vol": 48, "Volume": 70,
            "Float": 92, "$ Volume": 108, "Dollar Vol": 108,
            "Catalyst": 120, "Score": 44, "Setup": 62, "Status": 62,
            "Freshness": 220,
        }
        for index, name in enumerate(columns):
            self._table.setColumnWidth(index, widths.get(name, 118))

    def _set_view_filter(self, view: str) -> None:
        button = self._view_buttons.get(view)
        if button is None or not button.isEnabled():
            return
        self._active_view_filter = view
        for label, candidate in self._view_buttons.items():
            candidate.setChecked(label == view)
        self._last_render_fingerprint = None
        snapshot = getattr(self, "_source_snapshot", None)
        if snapshot is not None:
            self.render(snapshot)

    def _apply_search(self) -> None:
        snapshot = getattr(self, "_source_snapshot", None)
        if snapshot is not None:
            self._last_render_fingerprint = None
            self.render(snapshot)

    def set_paper_summary(self, summary: str, funnel: str, research: str) -> None:
        visible = self._mode_selector.currentText() == "WARRIOR PAPER"
        self._paper_summary.setText(summary)
        self._paper_funnel.setText(funnel)
        self._paper_research.setText(research)
        for label in (self._paper_summary, self._paper_funnel, self._paper_research):
            label.setVisible(visible)

    def _select_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._visible_rows):
            self.candidate_selected.emit(self._visible_rows[row].symbol)


class MarketWorkspace(QWidget):
    """Autonomous supervision workspace backed by immutable projections."""

    chart_symbol_selected = Signal(str)
    operator_symbol_selected = Signal(str)
    atlas_symbol_selected = Signal(str)
    chart_timeframe_selected = Signal(str)
    focus_mode_changed = Signal(bool)
    inspector_requested = Signal(bool)

    def __init__(self, chart_view: ChartView | None = None) -> None:
        super().__init__()
        if chart_view is not None and not isinstance(chart_view, QWidget):
            raise TypeError("chart_view must be a QWidget chart adapter")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Chart infrastructure remains available to non-workspace consumers,
        # but is deliberately not parented into the primary workstation.
        self.chart_view = chart_view
        self._chart_managed = False
        self._chart_snapshot = ChartViewSnapshot()

        self.watchlist = CompactWatchlistPanel()
        self.watchlist.candidate_selected.connect(self._select_candidate)
        self.watchlist.focus_mode_changed.connect(self._change_focus_mode)
        self.trade_intelligence = TradeIntelligencePanel()
        self._selected_symbol: str | None = None
        self._focus_mode = "CURRENT ATLAS"
        self._atlas_snapshot = WatchlistSnapshot()
        self._warrior_view = None

        self.atlas_activity = AtlasActivityPanel()
        self.activity_panel = ActivityPanel()
        self.portfolio_summary = PortfolioSummaryStrip()
        self.market_overview = MarketOverviewPanel()
        self.runtime_controls = RuntimeControlsPanel()
        self.runtime_controls.inspector_requested.connect(self._inspector_requested)
        self.ai_thinking = AIThinkingPanel()
        self.mission_status = MissionStatusPanel()
        self.infrastructure = InfrastructureStrip()

        self.ai_thinking_section = SectionPanel(
            "AI Thinking", self.ai_thinking, collapsible=True
        )
        self.activity_section = SectionPanel("LIVE AUTONOMOUS ACTIVITY", self.activity_panel)
        self.portfolio_section = SectionPanel(
            "PORTFOLIO / PERFORMANCE", self.portfolio_summary, scrollable=True
        )
        self.market_overview_section = SectionPanel(
            "MARKET OVERVIEW", self.market_overview, scrollable=True
        )
        self.runtime_controls_section = SectionPanel("RUNTIME CONTROLS", QWidget())
        # Compatibility-only reference for integrations that inspected the
        # former separate panel. Safety commands now live in the visible,
        # compact runtime strip and retain their original signal wiring.
        self.safety_section = SectionPanel("SAFETY", QWidget())
        self.safety_section.hide()
        self.mission_section = SectionPanel(
            "Mission Status", self.mission_status, collapsible=True
        )
        self.infrastructure_section = SectionPanel(
            "Infrastructure", self.infrastructure, collapsible=True
        )
        opportunities_action = QToolButton()
        opportunities_action.setObjectName("overflowButton")
        opportunities_action.setText("...")
        opportunities_action.setToolTip("Opportunity display settings")
        self.opportunities_section = SectionPanel(
            "OPPORTUNITIES", self.watchlist, action=opportunities_action
        )
        self.focus_section = self.opportunities_section
        self.opportunities_section.setMinimumWidth(0)
        self.market_section = SectionPanel(
            "ATLAS TRADE INTELLIGENCE",
            self.trade_intelligence,
            scrollable=True,
        )
        self.market_section.setMinimumWidth(0)
        self._layout_mode: str | None = None
        self._responsive_width_override: int | None = None

        self.left_stack = QWidget()
        self.left_column = self.left_stack
        left_layout = QVBoxLayout(self.left_stack)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setObjectName("workstationLeftSplitter")
        self.left_splitter.setHandleWidth(Dimensions.SPLITTER_HANDLE_WIDTH)
        self.left_splitter.addWidget(self.opportunities_section)
        self.left_splitter.addWidget(self.market_overview_section)
        self.left_splitter.setStretchFactor(0, 62)
        self.left_splitter.setStretchFactor(1, 38)
        self.left_splitter.setSizes((310, 190))
        left_layout.addWidget(self.left_splitter)

        # Compatibility alias for integrations that used the old wrapper name.
        # The Trade Intelligence shell itself is now the direct right child of
        # the production middle splitter, so no stale wrapper can detach it.
        self.right_workspace = self.market_section
        lower = QSplitter(Qt.Orientation.Horizontal)
        lower.addWidget(self.activity_section)
        lower.addWidget(self.portfolio_section)
        self.lower_splitter = lower
        lower.setHandleWidth(Dimensions.SPLITTER_HANDLE_WIDTH)
        lower.setStretchFactor(0, 53)
        lower.setStretchFactor(1, 47)
        lower.setSizes((810, 710))

        self.middle_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.middle_splitter.setObjectName("workstationMainSplitter")
        self.middle_splitter.setHandleWidth(Dimensions.SPLITTER_HANDLE_WIDTH)
        self.middle_splitter.addWidget(self.left_stack)
        self.middle_splitter.addWidget(self.market_section)
        for index in range(self.middle_splitter.count()):
            self.middle_splitter.setCollapsible(index, False)
        self.middle_splitter.setStretchFactor(0, 32)
        self.middle_splitter.setStretchFactor(1, 68)
        self.middle_splitter.setSizes((485, 1035))
        self.splitter = self.middle_splitter

        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setObjectName("workstationVerticalSplitter")
        self.workspace_splitter.setHandleWidth(Dimensions.SPLITTER_HANDLE_WIDTH)
        self.workspace_splitter.addWidget(self.middle_splitter)
        self.workspace_splitter.addWidget(lower)
        self.workspace_splitter.setStretchFactor(0, 61)
        self.workspace_splitter.setStretchFactor(1, 39)
        self.workspace_splitter.setSizes((500, 315))
        layout.addWidget(self.workspace_splitter)

        # Secondary intelligence remains available to the inspector dock.
        intelligence_rail = QWidget()
        intelligence_rail.setMinimumWidth(340)
        self.intelligence_rail = intelligence_rail
        rail_layout = QVBoxLayout(intelligence_rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(0)
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.setObjectName("intelligenceRailSplitter")
        self.right_splitter.setHandleWidth(Dimensions.SPLITTER_HANDLE_WIDTH)
        for section in (
            self.ai_thinking_section,
            SectionPanel("Atlas Activity", self.atlas_activity, collapsible=True),
            self.mission_section,
            self.infrastructure_section,
        ):
            self.right_splitter.addWidget(section)
            self.right_splitter.setCollapsible(
                self.right_splitter.indexOf(section), True
            )
        self.right_splitter.setSizes((230, 180, 190, 190))
        rail_layout.addWidget(self.right_splitter)

        self.top_splitter = self.splitter

    def _inspector_requested(self, visible: bool) -> None:
        self.inspector_requested.emit(visible)

    @property
    def chart_focused(self) -> bool:
        return False

    @property
    def layout_mode(self) -> str | None:
        return self._layout_mode

    def toggle_chart_focus(self) -> None:
        return

    def set_chart_focus(self, focused: bool) -> None:
        # Retained as a compatibility boundary for persisted pre-redesign
        # layouts. The primary workstation no longer has a chart focus mode.
        return

    def set_responsive_width(
        self, width: int, *, force: bool = False, external: bool = False
    ) -> None:
        if external:
            self._responsive_width_override = width
        mode = "compact" if width <= 1366 else "wide"
        if not force and mode == self._layout_mode:
            return
        self._layout_mode = mode
        left_minimum, intelligence_minimum = (
            (350, 600) if mode == "compact" else (400, 700)
        )
        self.left_stack.setMinimumWidth(left_minimum)
        self.market_section.setMinimumWidth(intelligence_minimum)
        self.splitter.setStretchFactor(0, 32)
        self.splitter.setStretchFactor(1, 68)
        self.splitter.setSizes(
            (410 if mode == "compact" else 485, 880 if mode == "compact" else 1035)
        )

    def ensure_middle_composition(self) -> None:
        """Keep the production 32/68 middle row usable after state restore."""
        self.market_section.show()
        self.trade_intelligence.show()
        available = max(
            0,
            self.middle_splitter.width() - self.middle_splitter.handleWidth(),
        )
        if available <= 0:
            return
        sizes = self.middle_splitter.sizes()
        left_minimum = self.left_stack.minimumWidth()
        intelligence_minimum = self.market_section.minimumWidth()
        invalid = (
            len(sizes) != 2
            or sizes[0] < left_minimum
            or sizes[1] < intelligence_minimum
        )
        if invalid:
            left = max(left_minimum, round(available * 0.32))
            right = available - left
            if right < intelligence_minimum:
                right = intelligence_minimum
                left = available - right
            self.middle_splitter.setSizes((left, right))

    def reset_layout(self, width: int | None = None) -> None:
        self.set_chart_focus(False)
        self.right_splitter.setSizes((230, 180, 190, 190))
        self.left_splitter.setSizes((310, 190))
        self.lower_splitter.setSizes((810, 710))
        self.workspace_splitter.setSizes((500, 315))
        self._layout_mode = None
        self.set_responsive_width(width or self.width(), force=True)

    def _emit_operator_symbol(self, symbol: str) -> None:
        self.operator_symbol_selected.emit(symbol)

    def _emit_atlas_symbol(self, symbol: str) -> None:
        self.atlas_symbol_selected.emit(symbol)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self._atlas_snapshot = snapshot
        if self._focus_mode == "WARRIOR PAPER":
            return
        self._render_focus(snapshot)

    def _render_focus(self, snapshot: WatchlistSnapshot) -> None:
        atlas_rows = tuple(row for row in snapshot.rows if row.rank != "--")
        if snapshot is self._atlas_snapshot:
            atlas_rows = tuple(self._with_warrior_state(row) for row in atlas_rows)
        symbols = {row.symbol for row in atlas_rows}
        projected = next((row.symbol for row in atlas_rows if row.selected), None)
        if self._selected_symbol not in symbols:
            self._selected_symbol = projected or (
                atlas_rows[0].symbol if atlas_rows else None
            )
        atlas_rows = tuple(
            replace(row, selected=row.symbol == self._selected_symbol)
            for row in atlas_rows
        )
        atlas_snapshot = replace(
            snapshot,
            rows=atlas_rows,
            candidate_count=len(atlas_rows),
        )
        self.watchlist.render(atlas_snapshot)
        selected = next(
            (row for row in atlas_rows if row.selected),
            None,
        )
        self.trade_intelligence.render(selected)

    def _select_candidate(self, symbol: str) -> None:
        if symbol == self._selected_symbol:
            return
        snapshot = (
            self._warrior_view.focus
            if self._focus_mode == "WARRIOR PAPER" and self._warrior_view is not None
            else self._atlas_snapshot
        )
        if not any(row.symbol == symbol for row in snapshot.rows):
            return
        self._selected_symbol = symbol
        self._render_focus(snapshot)
        self._emit_operator_symbol(symbol)

    def render_warrior(self, view) -> None:
        self._warrior_view = view
        if self._focus_mode == "WARRIOR PAPER":
            self._render_focus(view.focus)
            self.watchlist.set_paper_summary(view.summary, view.funnel, view.research)
        else:
            # Refresh Trade Intelligence for the selected scanner symbol while
            # preserving scanner rank, classification, and market evidence.
            self._render_focus(self._atlas_snapshot)

    def _with_warrior_state(self, scanner_row):
        view = self._warrior_view
        if view is None:
            return replace(
                scanner_row,
                warrior_evaluated=False,
                warrior_score="--",
                warrior_status="EVALUATING",
                warrior_session="--",
                strategy_name="Warrior Momentum",
                setup="--",
                setup_state="--",
                strategy_status="EVALUATING",
                entry_trigger="--",
                stop_price="--",
                blocking_reasons="--",
                decision_timestamp="--",
                decision_last="--",
                decision_bid="--",
                decision_ask="--",
                decision_spread="--",
            )
        match = next(
            (
                row for row in view.focus.rows
                if row.symbol.strip().upper() == scanner_row.symbol.strip().upper()
            ),
            None,
        )
        if match is None:
            status = (
                "EVALUATING"
                if getattr(view, "enabled", True)
                and getattr(view, "health", "RUNNING") not in {"DISABLED", "STOPPED"}
                else "UNAVAILABLE"
            )
            return replace(
                scanner_row,
                warrior_evaluated=False,
                warrior_score="--",
                warrior_status=status,
                warrior_session="--",
                strategy_name="Warrior Momentum",
                setup="--",
                setup_state="--",
                strategy_status=status,
                entry_trigger="--",
                stop_price="--",
                blocking_reasons="--",
                decision_timestamp="--",
                decision_last="--",
                decision_bid="--",
                decision_ask="--",
                decision_spread="--",
            )
        return replace(
            scanner_row,
            warrior_evaluated=True,
            warrior_score=match.warrior_score,
            warrior_status=match.warrior_status,
            warrior_session=match.warrior_session,
            strategy_name=match.strategy_name,
            setup=match.setup,
            setup_state=match.setup_state,
            strategy_status=match.strategy_status,
            entry_trigger=match.entry_trigger,
            stop_price=match.stop_price,
            blocking_reasons=match.blocking_reasons,
            decision_timestamp=match.decision_timestamp,
            decision_last=match.decision_last,
            decision_bid=match.decision_bid,
            decision_ask=match.decision_ask,
            decision_spread=match.decision_spread,
        )

    def _change_focus_mode(self, mode: str) -> None:
        self._focus_mode = mode
        if mode == "WARRIOR PAPER" and self._warrior_view is not None:
            self._render_focus(self._warrior_view.focus)
            self.watchlist.set_paper_summary(
                self._warrior_view.summary,
                self._warrior_view.funnel,
                self._warrior_view.research,
            )
        else:
            self._render_focus(self._atlas_snapshot)
            self.watchlist.set_paper_summary("", "", "")

    def set_chart_managed(self, managed: bool) -> None:
        self._chart_managed = bool(managed)

    def render_chart(self, snapshot: ChartViewSnapshot) -> None:
        self._chart_snapshot = snapshot
        if self.chart_view is not None:
            self.chart_view.render(snapshot)

    def render_activity(self, snapshot: AtlasActivitySnapshot) -> None:
        self.atlas_activity.render(snapshot)

    def render_ai_thinking(self, snapshot: AIThinkingSnapshot) -> None:
        self.ai_thinking.render(snapshot)

    def render_mission(self, snapshot: MissionStatusSnapshot) -> None:
        self.mission_status.render(snapshot)

    def render_infrastructure(self, snapshot: HealthDashboardSnapshot) -> None:
        self.infrastructure.render(snapshot)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 600)

    def resizeEvent(self, event) -> None:
        # Geometry changes only when crossing the real responsive breakpoint;
        # ordinary resizes preserve the operator's splitter choices.
        width = self._responsive_width_override or event.size().width()
        self.set_responsive_width(width)
        super().resizeEvent(event)


def _watchlist_color(column: str, value: str) -> str | None:
    normalized = value.upper()
    if column == "Chg %":
        if value.startswith("+"):
            return Colors.SUCCESS
        if value.startswith("-"):
            return Colors.DANGER
        return Colors.TEXT_MUTED
    if column in {"Status", "State"}:
        if "STALE" in normalized or "CLOSED" in normalized:
            return Colors.DANGER
        if "OPEN" in normalized and "LIVE" in normalized:
            return Colors.SUCCESS
        return Colors.TEXT_MUTED
    if column in {"Catalyst", "Setup / Catalyst"} and value != "--":
        return "#c084fc"
    return None


def _first_watchlist_value(*values: str) -> str:
    return next((value for value in values if value and value != "--"), "--")


__all__ = [
    "ChartPlaceholder",
    "ChartView",
    "CompactWatchlistPanel",
    "MarketWorkspace",
]

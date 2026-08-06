from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QDateTime, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.design.tokens import Colors, Dimensions
from app.gui.models import (
    AIThinkingSnapshot,
    AtlasActivitySnapshot,
    ChartViewSnapshot,
    WatchlistSnapshot,
)
from app.gui.widgets.atlas_activity_panel import AtlasActivityPanel
from app.gui.widgets.ai_thinking_panel import AIThinkingPanel
from app.gui.widgets.common import StatusIndicator
from app.gui.widgets.data_table import StyledDataTable
from app.gui.widgets.panel import SectionPanel


class ChartView(Protocol):
    def render(self, snapshot: ChartViewSnapshot) -> None: ...


class EmptyChartCanvas(QFrame):
    """Honest chart empty state with terminal-style grid treatment."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("chartCanvas")
        self.setMinimumHeight(Dimensions.CHART_MIN_HEIGHT)
        self._message = "No market series available."
        self._candles = ()

    def set_model(self, snapshot: ChartViewSnapshot) -> None:
        self._candles = snapshot.candles
        self._message = snapshot.message
        self.update()

    def set_message(self, message: str) -> None:
        self._message = message
        self._candles = ()
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(Colors.BACKGROUND))
        painter.setPen(QPen(QColor(Colors.CHART_GRID), 1))
        step_x = max(56, self.width() // 12)
        step_y = max(44, self.height() // 8)
        for x in range(0, self.width(), step_x):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step_y):
            painter.drawLine(0, y, self.width(), y)
        if self._candles:
            self._paint_candles(painter)
            return
        center_x = self.width() / 2
        center_y = self.height() / 2 - 28
        icon_pen = QPen(QColor(Colors.TEXT_FAINT), 2)
        painter.setPen(icon_pen)
        painter.drawRoundedRect(
            QRectF(center_x - 26, center_y - 28, 52, 42),
            6,
            6,
        )
        painter.drawLine(
            int(center_x - 16),
            int(center_y + 4),
            int(center_x - 5),
            int(center_y - 7),
        )
        painter.drawLine(
            int(center_x - 5),
            int(center_y - 7),
            int(center_x + 4),
            int(center_y),
        )
        painter.drawLine(
            int(center_x + 4),
            int(center_y),
            int(center_x + 16),
            int(center_y - 14),
        )
        lowered = self._message.lower()
        if "no active symbol" in lowered:
            title = "Atlas is currently scanning."
            detail = "No active trade is being visualized."
            hint = (
                "The chart will automatically display the highest-priority "
                "candidate or managed position."
            )
        elif "select" in lowered:
            title, detail = "Symbol not selected", "Select a symbol to initialize the chart"
            hint = "No market series has been fabricated."
        elif "subscription" in lowered:
            title, detail = "Waiting for subscription", self._message
            hint = "No market series has been fabricated."
        elif "entitlement" in lowered:
            title, detail = "Entitlement required", self._message
            hint = "No market series has been fabricated."
        else:
            title, detail = "Market data unavailable", self._message
            hint = "No market series has been fabricated."
        title_font = painter.font()
        title_font.setPixelSize(16)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(Colors.TEXT))
        painter.drawText(
            QRectF(0, center_y + 30, self.width(), 24),
            Qt.AlignmentFlag.AlignHCenter,
            title,
        )
        detail_font = painter.font()
        detail_font.setPixelSize(12)
        detail_font.setBold(False)
        painter.setFont(detail_font)
        painter.setPen(QColor(Colors.TEXT_MUTED))
        painter.drawText(
            QRectF(40, center_y + 56, self.width() - 80, 36),
            Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
            detail,
        )
        painter.setPen(QColor(Colors.TEXT_FAINT))
        painter.drawText(
            QRectF(50, center_y + 88, self.width() - 100, 44),
            Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
            hint,
        )

    def _paint_candles(self, painter: QPainter) -> None:
        candles = self._candles[-120:]
        highest = max(candle.high for candle in candles)
        lowest = min(candle.low for candle in candles)
        spread = highest - lowest
        if spread <= 0:
            return
        left, top, right, bottom = 18, 14, 18, 22
        width = max(1, self.width() - left - right)
        height = max(1, self.height() - top - bottom)
        step = width / max(1, len(candles))
        body_width = max(2.0, min(9.0, step * 0.62))

        def y(value) -> float:
            return top + float((highest - value) / spread) * height

        for index, candle in enumerate(candles):
            x = left + (index + 0.5) * step
            rising = candle.close >= candle.open
            color = QColor(Colors.SUCCESS if rising else Colors.DANGER)
            painter.setPen(QPen(color, 1))
            painter.drawLine(int(x), int(y(candle.high)), int(x), int(y(candle.low)))
            opened, closed = y(candle.open), y(candle.close)
            body_top = min(opened, closed)
            body_height = max(1.0, abs(opened - closed))
            painter.fillRect(
                QRectF(x - body_width / 2, body_top, body_width, body_height),
                color,
            )


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
        self._status = StatusIndicator("Unknown")
        self._timeframe = QComboBox()
        self._timeframe.addItems(("1m", "5m", "15m", "1H", "1D"))
        self._timeframe.setCurrentText("1D")
        indicators = QPushButton("Indicators")
        indicators.setObjectName("ghostButton")
        compare = QPushButton("Compare")
        compare.setObjectName("ghostButton")
        settings = QPushButton("⚙")
        settings.setObjectName("ghostButton")
        settings.setToolTip("Chart settings are unavailable without a chart engine")
        fullscreen = QPushButton("⛶")
        fullscreen.setObjectName("ghostButton")
        fullscreen.setToolTip("Fullscreen chart")
        toolbar.addWidget(self._symbol_selector)
        toolbar.addWidget(self._symbol)
        toolbar.addWidget(self._security_name)
        toolbar.addWidget(self._status)
        toolbar.addStretch()
        toolbar.addWidget(self._timeframe)
        toolbar.addWidget(indicators)
        toolbar.addWidget(compare)
        toolbar.addWidget(settings)
        toolbar.addWidget(fullscreen)
        layout.addLayout(toolbar)
        quote_row = QHBoxLayout()
        self._ohlc = QLabel("O —   H —   L —   C —")
        self._ohlc.setObjectName("monoValue")
        quote_row.addWidget(self._ohlc)
        quote_row.addStretch()
        layout.addLayout(quote_row)
        self._canvas = EmptyChartCanvas()
        layout.addWidget(self._canvas, 1)
        footer = QHBoxLayout()
        self._range_buttons = []
        for label in ("1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "5Y", "All"):
            button = QPushButton(label)
            button.setObjectName("ghostButton")
            button.setCheckable(True)
            button.setChecked(label == "1D")
            button.setToolTip(f"Show {label} range")
            self._range_buttons.append(button)
            footer.addWidget(button)
        footer.addStretch()
        self._market_time = QLabel()
        self._market_time.setObjectName("muted")
        footer.addWidget(self._market_time)
        for label in ("%", "log", "auto"):
            button = QPushButton(label)
            button.setObjectName("ghostButton")
            button.setToolTip(f"Chart {label} control")
            footer.addWidget(button)
        layout.addLayout(footer)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_time)
        self._clock.start(1000)
        self._update_time()

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

    def render(self, snapshot: ChartViewSnapshot) -> None:
        if snapshot.symbol != "--":
            self.select_symbol(snapshot.symbol)
        self._symbol.setText(snapshot.symbol)
        self._status.set_status(
            snapshot.market_status.title(),
            (
                "good"
                if snapshot.market_status.upper() == "OPEN"
                else "neutral"
            ),
        )
        self._timeframe.setCurrentText(snapshot.timeframe)
        self._ohlc.setText(
            "O {0}   H {1}   L {2}   C {3}".format(
                *(
                    "--" if value is None else f"{value:,.2f}"
                    for value in (
                        snapshot.open,
                        snapshot.high,
                        snapshot.low,
                        snapshot.close,
                    )
                )
            )
        )
        self._canvas.set_model(snapshot)


class CompactWatchlistPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(Dimensions.WATCHLIST_MIN_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = StyledDataTable(
            ("Symbol", "Last", "Change", "Change %")
        )
        self._table.set_empty_state(
            "Atlas is scanning the market.",
            "High-confidence opportunities\n"
            "will appear here automatically.",
            icon="\u2606",
        )
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        self._table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        layout.addWidget(self._table, 1)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self._table.set_empty_state(
            snapshot.empty_title,
            snapshot.empty_detail,
            icon="\u2606",
        )
        self._table.clearSelection()
        self._table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            state = (
                f"{row.market_status} \u00b7 {row.stale}"
                if row.market_status != "--"
                else row.stale
            )
            values = (
                f"\u25cf {row.symbol}" if row.selected else row.symbol,
                row.latest_price,
                row.change,
                row.change_percent,
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index in (1, 2, 3):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                color = _watchlist_color(column_index, value)
                if color is not None:
                    item.setForeground(QBrush(QColor(color)))
                item.setToolTip(state)
                self._table.setItem(
                    row_index,
                    column_index,
                    item,
                )
            if row.selected:
                self._table.selectRow(row_index)


class MarketWorkspace(QWidget):
    """Compose chart adapter and watchlist from one immutable snapshot."""

    chart_symbol_selected = Signal(str)
    chart_timeframe_selected = Signal(str)

    def __init__(self, chart_view: ChartView | None = None) -> None:
        super().__init__()
        if chart_view is not None and not isinstance(chart_view, QWidget):
            raise TypeError("chart_view must be a QWidget chart adapter")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(2)
        self.chart_view = chart_view or ChartPlaceholder()
        self._chart_managed = False
        self._chart_snapshot = ChartViewSnapshot()
        if isinstance(self.chart_view, ChartPlaceholder):
            self.chart_view._symbol_selector.currentTextChanged.connect(
                self.chart_symbol_selected.emit
            )
            self.chart_view._timeframe.currentTextChanged.connect(
                self.chart_timeframe_selected.emit
            )
        self.watchlist = CompactWatchlistPanel()
        self.atlas_activity = AtlasActivityPanel()
        self.ai_thinking = AIThinkingPanel()
        self.ai_thinking.setMinimumHeight(210)
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(7)
        self.ai_thinking_section = SectionPanel(
            "AI Thinking", self.ai_thinking
        )
        self.focus_section = SectionPanel("Atlas Focus", self.watchlist)
        self.activity_section = SectionPanel(
            "Atlas Activity", self.atlas_activity
        )
        sidebar_layout.addWidget(self.ai_thinking_section, 4)
        sidebar_layout.addWidget(self.focus_section, 3)
        sidebar_layout.addWidget(self.activity_section, 3)
        self.splitter.addWidget(
            SectionPanel("Market", self.chart_view)
        )
        self.splitter.addWidget(sidebar)
        self.splitter.setStretchFactor(0, 13)
        self.splitter.setStretchFactor(1, 7)
        self.splitter.setSizes((740, 400))
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        layout.addWidget(self.splitter)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self.watchlist.render(snapshot)
        selected = next(
            (row for row in snapshot.rows if row.selected),
            None,
        )
        if isinstance(self.chart_view, ChartPlaceholder):
            self.chart_view.set_symbols(
                tuple(row.symbol for row in snapshot.rows),
                selected.symbol if selected is not None else None,
            )
        if self._chart_managed:
            if (
                isinstance(self.chart_view, ChartPlaceholder)
                and self._chart_snapshot.symbol != "--"
            ):
                self.chart_view.select_symbol(self._chart_snapshot.symbol)
            return
        chart_snapshot = ChartViewSnapshot(
            symbol=selected.symbol if selected is not None else "--",
            timeframe="1D",
            market_status=(
                selected.market_status
                if selected is not None and selected.market_status != "--"
                else "UNKNOWN"
            ),
            message=(
                "Chart engine is not configured; market data is unavailable for this symbol."
                if selected is not None
                else "No active symbol is available for the market chart."
            ),
        )
        self.chart_view.render(chart_snapshot)

    def set_chart_managed(self, managed: bool) -> None:
        self._chart_managed = bool(managed)

    def render_chart(self, snapshot: ChartViewSnapshot) -> None:
        self._chart_snapshot = snapshot
        self.chart_view.render(snapshot)

    def render_activity(self, snapshot: AtlasActivitySnapshot) -> None:
        self.atlas_activity.render(snapshot)

    def render_ai_thinking(self, snapshot: AIThinkingSnapshot) -> None:
        self.ai_thinking.render(snapshot)

    def minimumSizeHint(self) -> QSize:
        # Preserve complete primary-panel content. The dashboard scroll area
        # handles shorter screens without allowing sidebar cards to overlap.
        return QSize(0, 650)

    def resizeEvent(self, event) -> None:
        self.splitter.setOrientation(
            Qt.Orientation.Vertical
            if event.size().width() < 820
            else Qt.Orientation.Horizontal
        )
        super().resizeEvent(event)


def _watchlist_color(column: int, value: str) -> str | None:
    normalized = value.upper()
    if column in (2, 3):
        if value.startswith("+"):
            return Colors.SUCCESS
        if value.startswith("-"):
            return Colors.DANGER
        return Colors.TEXT_MUTED
    if column == 4:
        if "STALE" in normalized or "CLOSED" in normalized:
            return Colors.DANGER
        if "OPEN" in normalized and "LIVE" in normalized:
            return Colors.SUCCESS
        return Colors.TEXT_MUTED
    return None


__all__ = [
    "ChartPlaceholder",
    "ChartView",
    "CompactWatchlistPanel",
    "MarketWorkspace",
]

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QDateTime, QRectF, QSize, Qt, QTimer
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
    ChartViewSnapshot,
    WatchlistSnapshot,
)
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

    def set_message(self, message: str) -> None:
        self._message = message
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
        if "select" in lowered:
            title, detail = "Symbol not selected", "Select a symbol to initialize the chart"
        elif "subscription" in lowered:
            title, detail = "Waiting for subscription", self._message
        elif "entitlement" in lowered:
            title, detail = "Entitlement required", self._message
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
            QRectF(0, center_y + 56, self.width(), 20),
            Qt.AlignmentFlag.AlignHCenter,
            detail,
        )
        painter.setPen(QColor(Colors.TEXT_FAINT))
        painter.drawText(
            QRectF(0, center_y + 78, self.width(), 20),
            Qt.AlignmentFlag.AlignHCenter,
            hint,
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

    def set_symbols(self, symbols: tuple[str, ...]) -> None:
        current = self._symbol_selector.currentText()
        self._symbol_selector.blockSignals(True)
        self._symbol_selector.clear()
        self._symbol_selector.addItems(symbols)
        if current in symbols:
            self._symbol_selector.setCurrentText(current)
        self._symbol_selector.setEnabled(bool(symbols))
        self._symbol_selector.blockSignals(False)

    def _update_time(self) -> None:
        self._market_time.setText(
            QDateTime.currentDateTime().toString("MMM d  hh:mm:ss AP")
        )

    def render(self, snapshot: ChartViewSnapshot) -> None:
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
        self._canvas.set_message(snapshot.message)


class CompactWatchlistPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(Dimensions.WATCHLIST_MIN_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        for label in ("+", "\u22ef"):
            button = QPushButton(label)
            button.setObjectName("ghostButton")
            button.setEnabled(False)
            button.setToolTip("No watchlist command boundary is configured.")
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self._table = StyledDataTable(
            ("Symbol", "Last", "Change", "Change %")
        )
        self._table.set_empty_state(
            "No symbols",
            "Start the runtime to discover scanner candidates.",
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
        self.watchlist = CompactWatchlistPanel()
        self.market_summary = MarketSummaryPanel()
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(7)
        sidebar_layout.addWidget(SectionPanel("Watchlist", self.watchlist), 3)
        sidebar_layout.addWidget(SectionPanel("Market Summary", self.market_summary), 2)
        self.splitter.addWidget(
            SectionPanel("Market", self.chart_view)
        )
        self.splitter.addWidget(sidebar)
        self.splitter.setStretchFactor(0, 7)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setSizes((780, 360))
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        layout.addWidget(self.splitter)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self.watchlist.render(snapshot)
        self.market_summary.render(snapshot)
        selected = next(
            (row for row in snapshot.rows if row.selected),
            snapshot.rows[0] if snapshot.rows else None,
        )
        if isinstance(self.chart_view, ChartPlaceholder):
            self.chart_view.set_symbols(tuple(row.symbol for row in snapshot.rows))
            if selected is not None:
                self.chart_view._symbol_selector.setCurrentText(selected.symbol)
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
                else "Select a symbol to initialize the market chart."
            ),
        )
        self.chart_view.render(chart_snapshot)

    def minimumSizeHint(self) -> QSize:
        # The splitter reflows vertically before dense chart controls can
        # impose their horizontal aggregate size on the dashboard shell.
        return QSize(0, 280)

    def resizeEvent(self, event) -> None:
        self.splitter.setOrientation(
            Qt.Orientation.Vertical
            if event.size().width() < 820
            else Qt.Orientation.Horizontal
        )
        super().resizeEvent(event)


class MarketSummaryPanel(QWidget):
    """Show benchmark quotes only when supplied by the watchlist projection."""

    SYMBOLS = ("SPY", "QQQ", "VIX", "DXY")

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._rows: dict[str, tuple[QLabel, QLabel]] = {}
        for symbol in self.SYMBOLS:
            row = QHBoxLayout()
            name = QLabel(symbol)
            name.setObjectName("monoValue")
            value = QLabel("—")
            value.setObjectName("monoValue")
            change = QLabel("—")
            change.setObjectName("muted")
            row.addWidget(name)
            row.addStretch()
            row.addWidget(value)
            row.addWidget(change)
            layout.addLayout(row)
            self._rows[symbol] = (value, change)
        layout.addStretch()

    def render(self, snapshot: WatchlistSnapshot) -> None:
        by_symbol = {row.symbol.upper(): row for row in snapshot.rows}
        for symbol, (value, change) in self._rows.items():
            quote = by_symbol.get(symbol)
            value.setText(quote.latest_price if quote is not None else "—")
            change_text = quote.change_percent if quote is not None else "—"
            change.setText(change_text)
            tone = _watchlist_color(3, change_text)
            change.setStyleSheet(f"color: {tone};" if tone else "")


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
    "MarketSummaryPanel",
]

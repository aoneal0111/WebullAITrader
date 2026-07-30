from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
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
        step_x = max(48, self.width() // 10)
        step_y = max(40, self.height() // 7)
        for x in range(0, self.width(), step_x):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step_y):
            painter.drawLine(0, y, self.width(), y)
        painter.setPen(QColor(Colors.TEXT_MUTED))
        painter.drawText(
            QRectF(self.rect()),
            Qt.AlignmentFlag.AlignCenter,
            self._message,
        )


class ChartPlaceholder(QWidget):
    """Polished placeholder behind the future chart-view boundary."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        toolbar = QHBoxLayout()
        self._symbol = QLabel("--")
        self._symbol.setObjectName("pageTitle")
        self._status = StatusIndicator("Unknown")
        self._timeframe = QComboBox()
        self._timeframe.addItems(("1m", "5m", "15m", "1H", "1D"))
        self._timeframe.setCurrentText("1D")
        indicators = QPushButton("Indicators")
        indicators.setObjectName("ghostButton")
        compare = QPushButton("Compare")
        compare.setObjectName("ghostButton")
        toolbar.addWidget(self._symbol)
        toolbar.addWidget(self._status)
        toolbar.addStretch()
        toolbar.addWidget(self._timeframe)
        toolbar.addWidget(indicators)
        toolbar.addWidget(compare)
        layout.addLayout(toolbar)
        self._canvas = EmptyChartCanvas()
        layout.addWidget(self._canvas, 1)

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
        self.setMinimumWidth(270)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        for label in ("+", "\u2212", "\u22ef"):
            button = QPushButton(label)
            button.setObjectName("ghostButton")
            button.setEnabled(False)
            button.setToolTip("No watchlist command boundary is configured.")
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self._table = StyledDataTable(
            ("Symbol", "Last", "Change", "Change %", "State")
        )
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        layout.addWidget(self._table, 1)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self._table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            values = (
                f"\u25cf {row.symbol}" if row.selected else row.symbol,
                row.latest_price,
                row.change,
                row.change_percent,
                (
                    f"{row.market_status} / {row.stale}"
                    if row.market_status != "--"
                    else row.stale
                ),
            )
            for column_index, value in enumerate(values):
                self._table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )


class MarketWorkspace(QWidget):
    """Compose chart adapter and watchlist from one immutable snapshot."""

    def __init__(self, chart_view: ChartView | None = None) -> None:
        super().__init__()
        self.setMaximumHeight(360)
        if chart_view is not None and not isinstance(chart_view, QWidget):
            raise TypeError("chart_view must be a QWidget chart adapter")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.chart_view = chart_view or ChartPlaceholder()
        self.watchlist = CompactWatchlistPanel()
        splitter.addWidget(
            SectionPanel("Market", self.chart_view)
        )
        splitter.addWidget(SectionPanel("Watchlist", self.watchlist))
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes((820, 320))
        layout.addWidget(splitter)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self.watchlist.render(snapshot)
        selected = next(
            (row for row in snapshot.rows if row.selected),
            snapshot.rows[0] if snapshot.rows else None,
        )
        chart_snapshot = ChartViewSnapshot(
            symbol=selected.symbol if selected is not None else "--",
            timeframe="1D",
            market_status=(
                selected.market_status
                if selected is not None and selected.market_status != "--"
                else "UNKNOWN"
            ),
            message=(
                "Chart engine is not configured. "
                "No candle data has been fabricated."
                if selected is not None
                else "Select a symbol to initialize the market chart."
            ),
        )
        self.chart_view.render(chart_snapshot)


__all__ = [
    "ChartPlaceholder",
    "ChartView",
    "CompactWatchlistPanel",
    "MarketWorkspace",
]

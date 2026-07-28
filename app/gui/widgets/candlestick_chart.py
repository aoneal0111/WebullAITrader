from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import (
    CandleInterval,
    CandleSeriesSnapshot,
    ChartMarker,
)
from app.gui.theme import Colors, Sizing


class ChartHeader(QWidget):
    interval_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.symbol_label = QLabel("No active symbol")
        self.symbol_label.setObjectName("sectionTitle")

        self.detail_label = QLabel("OHLCV - Not available")
        self.detail_label.setObjectName("muted")

        self.interval = QComboBox()
        self.interval.addItem("1 minute", CandleInterval.ONE_MINUTE)
        self.interval.addItem("5 minutes", CandleInterval.FIVE_MINUTES)
        self.interval.addItem("15 minutes", CandleInterval.FIFTEEN_MINUTES)
        self.interval.currentIndexChanged.connect(
            self._emit_interval_changed
        )

        layout.addWidget(self.symbol_label)
        layout.addWidget(self.detail_label)
        layout.addStretch(1)
        layout.addWidget(self.interval)

    def _emit_interval_changed(self) -> None:
        interval = self.interval.currentData()
        if interval is not None:
            self.interval_changed.emit(interval)


class ChartStatusBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.symbol_label = QLabel("Symbol: --")
        self.symbol_label.setObjectName("muted")

        self.interval_label = QLabel("Interval: --")
        self.interval_label.setObjectName("muted")

        self.candle_count_label = QLabel("Candles: 0")
        self.candle_count_label.setObjectName("muted")

        layout.addWidget(self.symbol_label)
        layout.addWidget(self.interval_label)
        layout.addWidget(self.candle_count_label)
        layout.addStretch(1)

    def render(self, snapshot: CandleSeriesSnapshot) -> None:
        symbol = snapshot.symbol or "--"
        self.symbol_label.setText(f"Symbol: {symbol}")
        self.interval_label.setText(
            f"Interval: {snapshot.interval.value}"
        )
        self.candle_count_label.setText(
            f"Candles: {len(snapshot.candles)}"
        )


class CandleCanvas(QWidget):
    """The existing read-only candlestick canvas, retained unchanged in role."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(Sizing.CHART_MIN_HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._snapshot = CandleSeriesSnapshot(
            None,
            CandleInterval.ONE_MINUTE,
        )
        self._markers: tuple[ChartMarker, ...] = ()

    def render(
        self,
        snapshot: CandleSeriesSnapshot,
        markers: tuple[ChartMarker, ...] = (),
    ) -> None:
        self._snapshot = snapshot
        self._markers = markers
        self.update()

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(Colors.SURFACE))
        candles = self._snapshot.candles

        if not candles:
            painter.setPen(QColor(Colors.TEXT_MUTED))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Waiting for market data",
            )
            return

        left = 42.0
        top = 16.0
        width = max(20.0, self.width() - 56.0)
        height = max(40.0, self.height() - 44.0)
        low = min(float(candle.low) for candle in candles)
        high = max(float(candle.high) for candle in candles)
        span = max(high - low, 0.01)
        step = width / max(len(candles), 1)

        def y(value: object) -> float:
            return top + (high - float(value)) / span * height

        for index, candle in enumerate(candles):
            x = left + index * step + step / 2
            color = QColor(
                Colors.SUCCESS
                if candle.close >= candle.open
                else Colors.DANGER
            )
            painter.setPen(QPen(color, 1.2))
            painter.drawLine(x, y(candle.high), x, y(candle.low))

            body_top, body_bottom = sorted(
                (y(candle.open), y(candle.close))
            )
            painter.fillRect(
                QRectF(
                    x - max(2.0, step * 0.28),
                    body_top,
                    max(3.0, step * 0.56),
                    max(1.0, body_bottom - body_top),
                ),
                color,
            )

        painter.setPen(QColor(Colors.BORDER_STRONG))
        painter.drawLine(
            left,
            top + height,
            left + width,
            top + height,
        )


class CandlestickChart(QWidget):
    interval_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.header = ChartHeader()
        self.canvas = CandleCanvas()
        self.status_bar = ChartStatusBar()

        self.symbol_label = self.header.symbol_label
        self.detail_label = self.header.detail_label
        self.interval = self.header.interval

        self.header.interval_changed.connect(
            self.interval_changed.emit
        )

        layout.addWidget(self.header)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.status_bar)

    def render(
        self,
        snapshot: CandleSeriesSnapshot,
        markers: tuple[ChartMarker, ...] = (),
    ) -> None:
        symbol = snapshot.symbol or "No active symbol"
        self.symbol_label.setText(symbol)
        self.detail_label.setText(
            f"OHLCV - {len(snapshot.candles)} candles"
        )
        self.canvas.render(snapshot, markers)
        self.status_bar.render(snapshot)



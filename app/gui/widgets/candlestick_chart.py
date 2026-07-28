from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.gui.models import CandleInterval, CandleSeriesSnapshot, ChartMarker


class CandleCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._snapshot = CandleSeriesSnapshot(None, CandleInterval.ONE_MINUTE)
        self._markers: tuple[ChartMarker, ...] = ()

    def render(self, snapshot: CandleSeriesSnapshot, markers: tuple[ChartMarker, ...] = ()) -> None:
        self._snapshot, self._markers = snapshot, markers
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#10151d"))
        candles = self._snapshot.candles
        if not candles:
            painter.setPen(QColor("#8f9aaa"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for live market data")
            return
        left, top, width, height = 42.0, 16.0, max(20.0, self.width() - 56.0), max(40.0, self.height() - 44.0)
        lo = min(float(c.low) for c in candles)
        hi = max(float(c.high) for c in candles)
        span = max(hi - lo, 0.01)
        step = width / max(len(candles), 1)
        for index, candle in enumerate(candles):
            x = left + index * step + step / 2
            y = lambda value: top + (hi - float(value)) / span * height
            rising = candle.close >= candle.open
            color = QColor("#34d399" if rising else "#f87171")
            painter.setPen(QPen(color, 1.2))
            painter.drawLine(x, y(candle.high), x, y(candle.low))
            body_top, body_bottom = sorted((y(candle.open), y(candle.close)))
            painter.fillRect(QRectF(x - max(2.0, step * 0.28), body_top, max(3.0, step * 0.56), max(1.0, body_bottom - body_top)), color)
        painter.setPen(QColor("#526174"))
        painter.drawLine(left, top + height, left + width, top + height)


class CandlestickChart(QWidget):
    interval_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.symbol_label = QLabel("No active symbol")
        self.symbol_label.setObjectName("sectionTitle")
        self.detail_label = QLabel("OHLCV — Not available")
        self.detail_label.setObjectName("muted")
        self.interval = QComboBox()
        self.interval.addItem("1 minute", CandleInterval.ONE_MINUTE)
        self.interval.addItem("5 minutes", CandleInterval.FIVE_MINUTES)
        self.interval.addItem("15 minutes", CandleInterval.FIFTEEN_MINUTES)
        self.interval.currentIndexChanged.connect(lambda _: self.interval_changed.emit(self.interval.currentData()))
        header.addWidget(self.symbol_label)
        header.addStretch()
        header.addWidget(self.detail_label)
        header.addWidget(self.interval)
        layout.addLayout(header)
        self.canvas = CandleCanvas()
        layout.addWidget(self.canvas, 1)

    def render(self, snapshot: CandleSeriesSnapshot, markers: tuple[ChartMarker, ...] = ()) -> None:
        self.symbol_label.setText(snapshot.symbol or "No active symbol")
        if snapshot.candles:
            candle = snapshot.candles[-1]
            self.detail_label.setText(f"O {candle.open}  H {candle.high}  L {candle.low}  C {candle.close}  V {candle.volume}")
        else:
            self.detail_label.setText("OHLCV — Not available")
        self.canvas.render(snapshot, markers)

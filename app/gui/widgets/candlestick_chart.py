from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class ChartViewport:
    """Immutable geometry and price range for one chart render."""

    left: float
    top: float
    width: float
    height: float
    low: float
    high: float
    step: float


@dataclass(frozen=True, slots=True)
class ChartTransform:
    """Convert chart data coordinates into canvas coordinates."""

    viewport: ChartViewport

    def price_to_y(self, value: object) -> float:
        span = max(self.viewport.high - self.viewport.low, 0.01)
        return (
            self.viewport.top
            + (self.viewport.high - float(value))
            / span
            * self.viewport.height
        )

    def index_to_x(self, index: int) -> float:
        return (
            self.viewport.left
            + index * self.viewport.step
            + self.viewport.step / 2
        )

    def y_to_price(self, y: float) -> float:
        span = max(self.viewport.high - self.viewport.low, 0.01)
        ratio = (y - self.viewport.top) / self.viewport.height
        return self.viewport.high - ratio * span

    def x_to_index(self, x: float, candle_count: int) -> int:
        if candle_count <= 0:
            return 0

        raw_index = int(
            (x - self.viewport.left)
            / max(self.viewport.step, 0.01)
        )
        return max(0, min(raw_index, candle_count - 1))


@dataclass(frozen=True, slots=True)
class ChartLayout:
    left: float = 42.0
    top: float = 16.0
    right: float = 14.0
    bottom: float = 28.0
    minimum_width: float = 20.0
    minimum_height: float = 40.0

    def drawable_rect(
        self,
        canvas_width: float,
        canvas_height: float,
    ) -> tuple[float, float, float, float]:
        return (
            self.left,
            self.top,
            max(self.minimum_width, canvas_width - self.left - self.right),
            max(self.minimum_height, canvas_height - self.top - self.bottom),
        )


@dataclass(frozen=True, slots=True)
class ChartCamera:
    """Build the viewport used for one chart render."""

    layout: ChartLayout = ChartLayout()

    def build_viewport(
        self,
        canvas_width: float,
        canvas_height: float,
        candles: tuple,
    ) -> ChartViewport:
        left, top, width, height = self.layout.drawable_rect(
            canvas_width,
            canvas_height,
        )
        low = min(float(candle.low) for candle in candles)
        high = max(float(candle.high) for candle in candles)
        step = width / max(len(candles), 1)

        return ChartViewport(
            left=left,
            top=top,
            width=width,
            height=height,
            low=low,
            high=high,
            step=step,
        )




class ChartRenderer:
    """Render a candlestick chart onto a QPainter."""

    def render(
        self,
        painter: QPainter,
        viewport: ChartViewport,
        snapshot: CandleSeriesSnapshot,
        markers: tuple[ChartMarker, ...],
        cursor_position: tuple[float, float] | None,
    ) -> None:
        self._draw_grid(
            painter,
            viewport,
        )
        self._draw_axes(
            painter,
            viewport,
        )
        self._draw_candles(
            painter,
            viewport,
            snapshot,
        )
        self._draw_markers(
            painter,
            viewport,
            markers,
        )
        self._draw_overlay(
            painter,
            viewport,
            snapshot,
            cursor_position,
        )


    def _draw_grid(
        self,
        painter: QPainter,
        viewport: ChartViewport,
    ) -> None:
        del painter, viewport

    def _draw_axes(
        self,
        painter: QPainter,
        viewport: ChartViewport,
    ) -> None:
        left = viewport.left
        top = viewport.top
        width = viewport.width
        height = viewport.height
        painter.setPen(QColor(Colors.BORDER_STRONG))
        painter.drawLine(
            left,
            top + height,
            left + width,
            top + height,
        )

    def _draw_candles(
        self,
        painter: QPainter,
        viewport: ChartViewport,
        snapshot: CandleSeriesSnapshot,
    ) -> None:
        candles = snapshot.candles
        transform = ChartTransform(viewport)
        step = viewport.step

        for index, candle in enumerate(candles):
            x = transform.index_to_x(index)
            color = QColor(
                Colors.SUCCESS
                if candle.close >= candle.open
                else Colors.DANGER
            )
            painter.setPen(QPen(color, 1.2))

            high_y = transform.price_to_y(candle.high)
            low_y = transform.price_to_y(candle.low)
            painter.drawLine(x, high_y, x, low_y)

            open_y = transform.price_to_y(candle.open)
            close_y = transform.price_to_y(candle.close)
            body_top, body_bottom = sorted((open_y, close_y))

            painter.fillRect(
                QRectF(
                    x - max(2.0, step * 0.28),
                    body_top,
                    max(3.0, step * 0.56),
                    max(1.0, body_bottom - body_top),
                ),
                color,
            )


    def _draw_markers(
        self,
        painter: QPainter,
        viewport: ChartViewport,
        markers: tuple[ChartMarker, ...],
    ) -> None:
        del painter, viewport, markers

    def _draw_overlay(
        self,
        painter: QPainter,
        viewport: ChartViewport,
        snapshot: CandleSeriesSnapshot,
        cursor_position: tuple[float, float] | None,
    ) -> None:
        if cursor_position is None:
            return

        cursor_x, cursor_y = cursor_position

        right = viewport.left + viewport.width
        bottom = viewport.top + viewport.height

        if not (
            viewport.left <= cursor_x <= right
            and viewport.top <= cursor_y <= bottom
        ):
            return

        transform = ChartTransform(viewport)
        candles = snapshot.candles
        candle_index = transform.x_to_index(
            cursor_x,
            len(candles),
        )
        snapped_x = transform.index_to_x(candle_index)
        price = transform.y_to_price(cursor_y)

        crosshair_pen = QPen(QColor(Colors.TEXT_MUTED), 1.0)
        crosshair_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(crosshair_pen)

        painter.drawLine(
            snapped_x,
            viewport.top,
            snapped_x,
            bottom,
        )
        painter.drawLine(
            viewport.left,
            cursor_y,
            right,
            cursor_y,
        )

        price_text = f"{price:.2f}"
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(price_text)
        text_height = metrics.height()

        label_width = text_width + 12
        label_height = text_height + 6
        label_x = max(
            viewport.left,
            right - label_width,
        )
        label_y = max(
            viewport.top,
            min(
                cursor_y - label_height / 2,
                bottom - label_height,
            ),
        )

        label_rect = QRectF(
            label_x,
            label_y,
            label_width,
            label_height,
        )

        painter.fillRect(
            label_rect,
            QColor(Colors.SURFACE),
        )
        painter.setPen(QColor(Colors.TEXT_PRIMARY))
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignCenter,
            price_text,
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
        self._cursor_position: tuple[float, float] | None = None
        self._camera = ChartCamera()
        self._renderer = ChartRenderer()
        self.setMouseTracking(True)

    def render(
        self,
        snapshot: CandleSeriesSnapshot,
        markers: tuple[ChartMarker, ...] = (),
    ) -> None:
        self._snapshot = snapshot
        self._markers = markers
        self.update()

    def mouseMoveEvent(self, event: object) -> None:
        position = event.position()
        self._cursor_position = (
            float(position.x()),
            float(position.y()),
        )
        self.update()

    def leaveEvent(self, event: object) -> None:
        del event
        self._cursor_position = None
        self.update()

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)

        self._draw_background(painter)

        if not self._snapshot.candles:
            self._draw_empty_state(painter)
            return

        viewport = self._build_viewport()
        self._renderer.render(
            painter,
            viewport,
            self._snapshot,
            self._markers,
            self._cursor_position,
        )

    def _draw_background(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(Colors.SURFACE))

    def _draw_empty_state(self, painter: QPainter) -> None:
        painter.setPen(QColor(Colors.TEXT_MUTED))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "Waiting for market data",
        )

    def _build_viewport(self) -> ChartViewport:
        return self._camera.build_viewport(
            canvas_width=float(self.width()),
            canvas_height=float(self.height()),
            candles=self._snapshot.candles,
        )

    def _draw_grid(
        self,
        painter: QPainter,
        viewport: ChartViewport,
    ) -> None:
        del painter, viewport



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

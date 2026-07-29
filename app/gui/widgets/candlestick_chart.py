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
    """Margins reserved for the plot and its external axis labels."""

    left: float = 12.0
    top: float = 16.0
    right: float = 72.0
    bottom: float = 36.0
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


@dataclass(slots=True)
class ChartCamera:
    """Own the visible candle range and build its render viewport."""

    layout: ChartLayout = ChartLayout()
    vertical_padding_ratio: float = 0.08
    minimum_price_span: float = 0.01
    minimum_visible_candles: int = 12
    zoom_step_ratio: float = 0.16
    visible_start: int = 0
    visible_count: int | None = None

    def reset(self, candle_count: int) -> None:
        """Fit every available candle into the viewport."""

        self.visible_start = 0
        self.visible_count = max(candle_count, 0)

    def visible_range(self, candle_count: int) -> tuple[int, int]:
        """Return a clamped half-open range into the full candle series."""

        if candle_count <= 0:
            return 0, 0

        if self.visible_count is None:
            self.reset(candle_count)

        count = max(1, min(int(self.visible_count or 1), candle_count))
        start = max(0, min(self.visible_start, candle_count - count))
        self.visible_start = start
        self.visible_count = count
        return start, start + count

    def zoom_at(
        self,
        wheel_steps: float,
        cursor_x: float,
        canvas_width: float,
        candle_count: int,
    ) -> bool:
        """Zoom horizontally while keeping the cursor's candle anchored."""

        if candle_count <= 1 or wheel_steps == 0:
            return False

        start, end = self.visible_range(candle_count)
        old_count = end - start
        minimum = min(self.minimum_visible_candles, candle_count)
        scale = 1.0 - self.zoom_step_ratio * wheel_steps
        new_count = round(old_count * max(0.2, scale))
        new_count = max(minimum, min(new_count, candle_count))
        if new_count == old_count:
            return False

        left, _, width, _ = self.layout.drawable_rect(
            canvas_width,
            0.0,
        )
        cursor_ratio = (cursor_x - left) / max(width, 1.0)
        cursor_ratio = max(0.0, min(cursor_ratio, 1.0))
        anchor_index = start + cursor_ratio * old_count
        new_start = round(anchor_index - cursor_ratio * new_count)
        new_start = max(0, min(new_start, candle_count - new_count))

        self.visible_start = new_start
        self.visible_count = new_count
        return True

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
        raw_low = min(float(candle.low) for candle in candles)
        raw_high = max(float(candle.high) for candle in candles)
        raw_span = max(raw_high - raw_low, self.minimum_price_span)
        padding = raw_span * self.vertical_padding_ratio
        low = raw_low - padding
        high = raw_high + padding
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


@dataclass(frozen=True, slots=True)
class ChartRenderContext:
    painter: QPainter
    viewport: ChartViewport
    snapshot: CandleSeriesSnapshot
    markers: tuple[ChartMarker, ...]
    cursor_position: tuple[float, float] | None


class ChartRenderer:
    """Render a candlestick chart onto a QPainter."""

    minimum_body_width = 1.5
    maximum_body_width = 18.0
    body_fill_ratio = 0.68
    minimum_body_height = 1.5

    def render(
        self,
        context: ChartRenderContext,
    ) -> None:
        context.painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )
        self._draw_grid(context)
        self._draw_axes(context)
        self._draw_candles(context)
        self._draw_last_price(context)
        self._draw_markers(context)
        self._draw_overlay(context)

    def _draw_grid(
        self,
        context: ChartRenderContext,
    ) -> None:
        """Draw responsive plot guides behind the market data."""

        painter = context.painter
        viewport = context.viewport
        candles = context.snapshot.candles

        left = viewport.left
        top = viewport.top
        right = left + viewport.width
        bottom = top + viewport.height
        price_divisions = self._price_divisions(viewport.height)

        painter.setPen(QPen(QColor(Colors.CHART_GRID), 1.0))

        for tick in range(price_divisions + 1):
            ratio = tick / price_divisions
            y = top + ratio * viewport.height
            painter.drawLine(left, y, right, y)

        if len(candles) < 2:
            return

        time_divisions = self._time_divisions(viewport.width)
        transform = ChartTransform(viewport)
        last_index = len(candles) - 1

        for tick in range(time_divisions + 1):
            index = round(tick * last_index / time_divisions)
            x = transform.index_to_x(index)
            x = max(left, min(x, right))
            painter.drawLine(x, top, x, bottom)

    def _draw_axes(
        self,
        context: ChartRenderContext,
    ) -> None:
        """Draw the right-side price scale and bottom time scale."""

        painter = context.painter
        viewport = context.viewport
        candles = context.snapshot.candles

        left = viewport.left
        top = viewport.top
        right = left + viewport.width
        bottom = top + viewport.height
        axis_pen = QPen(QColor(Colors.CHART_AXIS), 1.0)

        painter.setPen(axis_pen)
        painter.drawLine(left, bottom, right, bottom)
        painter.drawLine(right, top, right, bottom)

        metrics = painter.fontMetrics()
        label_height = float(metrics.height() + 4)
        price_divisions = self._price_divisions(viewport.height)
        price_span = max(viewport.high - viewport.low, 0.01)

        for tick in range(price_divisions + 1):
            ratio = tick / price_divisions
            y = top + ratio * viewport.height
            price = viewport.high - ratio * price_span
            price_text = f"{price:.2f}"

            painter.setPen(axis_pen)
            painter.drawLine(right, y, right + 5.0, y)

            price_rect = QRectF(
                right + 8.0,
                y - label_height / 2.0,
                58.0,
                label_height,
            )
            painter.setPen(QColor(Colors.TEXT_MUTED))
            painter.drawText(
                price_rect,
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter,
                price_text,
            )

        if not candles:
            return

        time_divisions = self._time_divisions(viewport.width)
        transform = ChartTransform(viewport)
        last_index = len(candles) - 1

        for tick in range(time_divisions + 1):
            index = round(tick * last_index / time_divisions)
            candle = candles[index]
            x = transform.index_to_x(index)
            x = max(left, min(x, right))
            time_text = candle.timestamp.strftime("%H:%M")
            text_width = float(metrics.horizontalAdvance(time_text))
            label_x = max(
                left,
                min(x - text_width / 2.0, right - text_width),
            )

            painter.setPen(axis_pen)
            painter.drawLine(x, bottom, x, bottom + 5.0)

            time_rect = QRectF(
                label_x,
                bottom + 7.0,
                text_width,
                label_height,
            )
            painter.setPen(QColor(Colors.TEXT_MUTED))
            painter.drawText(
                time_rect,
                Qt.AlignmentFlag.AlignCenter,
                time_text,
            )

    @staticmethod
    def _price_divisions(viewport_height: float) -> int:
        return 4 if viewport_height < 280.0 else 6

    @staticmethod
    def _time_divisions(viewport_width: float) -> int:
        if viewport_width < 420.0:
            return 2
        if viewport_width < 700.0:
            return 4
        return 6

    def _draw_candles(
        self,
        context: ChartRenderContext,
    ) -> None:
        painter = context.painter
        viewport = context.viewport
        candles = context.snapshot.candles
        transform = ChartTransform(viewport)
        body_width = self._body_width(viewport.step)

        for index, candle in enumerate(candles):
            x = transform.index_to_x(index)
            color = self._candle_color(candle)
            wick_color = QColor(color)
            wick_color.setAlpha(210)

            high_y = transform.price_to_y(candle.high)
            low_y = transform.price_to_y(candle.low)
            painter.setPen(QPen(wick_color, 1.0))
            painter.drawLine(x, high_y, x, low_y)

            open_y = transform.price_to_y(candle.open)
            close_y = transform.price_to_y(candle.close)
            body_top, body_bottom = sorted((open_y, close_y))
            body_height = max(
                self.minimum_body_height,
                body_bottom - body_top,
            )
            body_rect = QRectF(
                x - body_width / 2.0,
                body_top,
                body_width,
                body_height,
            )

            painter.setPen(QPen(color, 1.0))
            painter.fillRect(body_rect, color)
            painter.drawRect(body_rect)

    def _draw_last_price(
        self,
        context: ChartRenderContext,
    ) -> None:
        candles = context.snapshot.candles
        if not candles:
            return

        painter = context.painter
        viewport = context.viewport
        transform = ChartTransform(viewport)
        last_candle = candles[-1]
        last_price = float(last_candle.close)
        y = transform.price_to_y(last_price)
        right = viewport.left + viewport.width
        color = self._candle_color(last_candle)

        line_pen = QPen(color, 1.0)
        line_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(line_pen)
        painter.drawLine(viewport.left, y, right, y)

        price_text = f"{last_price:.2f}"
        metrics = painter.fontMetrics()
        label_width = float(metrics.horizontalAdvance(price_text) + 14)
        label_height = float(metrics.height() + 6)
        label_rect = QRectF(
            right + 1.0,
            y - label_height / 2.0,
            min(68.0, label_width),
            label_height,
        )

        painter.fillRect(label_rect, color)
        painter.setPen(QColor(Colors.SURFACE))
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignCenter,
            price_text,
        )

    def _body_width(self, step: float) -> float:
        return max(
            self.minimum_body_width,
            min(self.maximum_body_width, step * self.body_fill_ratio),
        )

    @staticmethod
    def _candle_color(candle: object) -> QColor:
        return QColor(
            Colors.SUCCESS
            if candle.close >= candle.open
            else Colors.DANGER
        )

    def _draw_markers(
        self,
        context: ChartRenderContext,
    ) -> None:
        painter = context.painter
        viewport = context.viewport
        markers = context.markers
        del painter, viewport, markers

    def _draw_overlay(
        self,
        context: ChartRenderContext,
    ) -> None:
        painter = context.painter
        viewport = context.viewport
        snapshot = context.snapshot
        cursor_position = context.cursor_position
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
        previous_count = len(self._snapshot.candles)
        series_changed = (
            snapshot.symbol != self._snapshot.symbol
            or snapshot.interval != self._snapshot.interval
        )
        was_fit_all = (
            self._camera.visible_start == 0
            and (
                self._camera.visible_count is None
                or self._camera.visible_count >= previous_count
            )
        )
        self._snapshot = snapshot
        self._markers = markers
        if series_changed or was_fit_all:
            self._camera.reset(len(snapshot.candles))
        else:
            self._camera.visible_range(len(snapshot.candles))
        self.update()

    def wheelEvent(self, event: object) -> None:
        position = event.position()
        wheel_steps = float(event.angleDelta().y()) / 120.0
        changed = self._camera.zoom_at(
            wheel_steps=wheel_steps,
            cursor_x=float(position.x()),
            canvas_width=float(self.width()),
            candle_count=len(self._snapshot.candles),
        )
        if changed:
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event: object) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._camera.reset(len(self._snapshot.candles))
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

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

        visible_snapshot = self._visible_snapshot()
        viewport = self._build_viewport(visible_snapshot.candles)

        context = ChartRenderContext(
            painter=painter,
            viewport=viewport,
            snapshot=visible_snapshot,
            markers=self._markers,
            cursor_position=self._cursor_position,
        )

        self._renderer.render(context)

    def _draw_background(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(Colors.SURFACE))

    def _draw_empty_state(self, painter: QPainter) -> None:
        painter.setPen(QColor(Colors.TEXT_MUTED))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            "Waiting for market data",
        )

    def _visible_snapshot(self) -> CandleSeriesSnapshot:
        start, end = self._camera.visible_range(
            len(self._snapshot.candles)
        )
        return CandleSeriesSnapshot(
            symbol=self._snapshot.symbol,
            interval=self._snapshot.interval,
            candles=self._snapshot.candles[start:end],
            venue=self._snapshot.venue,
        )

    def _build_viewport(self, candles: tuple) -> ChartViewport:
        return self._camera.build_viewport(
            canvas_width=float(self.width()),
            canvas_height=float(self.height()),
            candles=candles,
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

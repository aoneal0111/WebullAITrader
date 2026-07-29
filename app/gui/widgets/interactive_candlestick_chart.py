from __future__ import annotations

from PySide6.QtGui import QColor, QPainter

from app.gui.models import CandleSeriesSnapshot
from app.gui.theme import Colors
from app.gui.widgets.candlestick_chart import (
    CandleCanvas,
    CandlestickChart,
    ChartRenderContext,
)


class ZoomableCandleCanvas(CandleCanvas):
    """Candlestick canvas with cursor-centered
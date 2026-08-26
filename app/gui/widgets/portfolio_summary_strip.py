from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from app.gui.models import PortfolioDashboardSnapshot
from app.gui.widgets.common import MetricCard


class PortfolioSummaryStrip(QWidget):
    """Render presenter-formatted portfolio values with visual hierarchy."""

    def __init__(self) -> None:
        super().__init__()
        # Initialize resize-event state before installing the layout; native
        # resize delivery can occur while child widgets are being constructed.
        self._cards = {}
        self._card_order = []
        self._columns = 3
        self.setMinimumHeight(264)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(6)

        specifications = (
            ("Equity", "Net Equity", "primary"),
            ("Cash", "Cash", "medium"),
            ("Buying Power", "Buying Power", "medium"),
            ("Total P/L", "Total PnL (Day)", "primary"),
            ("Unrealized P/L", "Unrealized PnL", "primary"),
            ("Realized P/L", "Realized PnL", "primary"),
            ("Open Positions", "Positions", "standard"),
            ("Exposure", "Exposure", "standard"),
            ("Gross Exposure", "Gross Exposure", "standard"),
            ("Net Exposure", "Net Exposure", "standard"),
            ("Current Drawdown", "Current Drawdown", "standard"),
            (
                "Winning / Losing Positions",
                "Winning / Losing Positions",
                "standard",
            ),
            ("Win Rate", "Win Rate", "standard"),
        )
        for index, (source, title, emphasis) in enumerate(specifications):
            card = MetricCard(title, emphasis=emphasis)
            if source == "Open Positions":
                card._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._layout.addWidget(card, index // 3, index % 3)
            self._cards[source] = card
            self._card_order.append(card)
        for column in range(3):
            self._layout.setColumnStretch(column, 1)

    def minimumSizeHint(self) -> QSize:
        # Two compact rows on wide screens; additional rows are allowed on
        # smaller screens instead of clipping metric text.
        columns = max(1, getattr(self, "_columns", 3) or 3)
        rows = max(1, (len(self._card_order) + columns - 1) // columns)
        return QSize(0, 48 * rows + 6 * max(0, rows - 1))

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        # The workstation gives this panel roughly 40% of the bottom row.
        # Three columns preserve a label and a prominent value without
        # producing the seven-row stack that previously drove compression.
        columns = 2 if width < 340 else 3
        if columns != getattr(self, "_columns", 3):
            self._columns = columns
            for column in range(len(self._card_order)):
                self._layout.setColumnStretch(column, 0)
            for index, card in enumerate(self._card_order):
                self._layout.addWidget(card, index // columns, index % columns)
            for column in range(columns):
                self._layout.setColumnStretch(column, 1)
            self.updateGeometry()
        super().resizeEvent(event)

    def render(self, snapshot: PortfolioDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
        metrics.update(snapshot.highlights)
        for source, card in self._cards.items():
            value = metrics.get(source, "--")
            card.set_value(value)
            card.set_tone(
                _value_tone(value)
                if "P/L" in source
                else "neutral"
                if value == "--"
                else "standard"
            )


def _value_tone(value: str) -> str:
    if value.startswith("+"):
        return "good"
    if value.startswith("-"):
        return "danger"
    return "neutral" if value in {"--", "$0.00", "0"} else "standard"


__all__ = ["PortfolioSummaryStrip"]

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from app.gui.models import PortfolioDashboardSnapshot
from app.gui.widgets.common import MetricCard


class PortfolioSummaryStrip(QWidget):
    """Render presenter-formatted portfolio values with visual hierarchy."""

    def __init__(self) -> None:
        super().__init__()
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(8)

        specifications = (
            ("Equity", "Net Liquidity", "primary"),
            ("Buying Power", "Buying Power", "medium"),
            ("Total P/L", "Total PnL (Day)", "primary"),
            ("Unrealized P/L", "Unrealized PnL", "standard"),
            ("Realized P/L", "Realized PnL", "standard"),
            ("Open Positions", "Positions", "standard"),
            ("Exposure", "Exposure", "standard"),
        )
        self._cards = {}
        self._card_order = []
        for column, (source, title, emphasis) in enumerate(specifications):
            card = MetricCard(title, emphasis=emphasis)
            if source == "Open Positions":
                card._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._layout.addWidget(card, 0, column)
            self._cards[source] = card
            self._card_order.append(card)
            self._layout.setColumnStretch(column, 1)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 176)

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        columns = 2 if width < 620 else 4 if width < 1100 else 7
        for index, card in enumerate(self._card_order):
            self._layout.addWidget(card, index // columns, index % columns)
        super().resizeEvent(event)

    def render(self, snapshot: PortfolioDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
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

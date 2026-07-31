from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from app.gui.models import PortfolioDashboardSnapshot
from app.gui.widgets.common import MetricCard


class PortfolioSummaryStrip(QWidget):
    """Render presenter-formatted portfolio values with visual hierarchy."""

    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(0)

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
        for column, (source, title, emphasis) in enumerate(specifications):
            card = MetricCard(title, emphasis=emphasis)
            if source == "Open Positions":
                card._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(card, 0, column)
            self._cards[source] = card
            layout.setColumnStretch(column, 1)

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

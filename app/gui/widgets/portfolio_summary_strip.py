from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from app.gui.models import PortfolioDashboardSnapshot
from app.gui.widgets.common import MetricCard


class PortfolioSummaryStrip(QWidget):
    """Render only portfolio values prepared by PortfolioPresenter."""

    _FIELDS = (
        ("Equity", "Net Liquidity"),
        ("Buying Power", "Buying Power"),
        ("Total P/L", "Total P&L"),
        ("Unrealized P/L", "Unrealized"),
        ("Realized P/L", "Realized"),
        ("Open Positions", "Positions"),
        ("Gross Exposure", "Exposure"),
        ("Working Orders", "Working Orders"),
    )

    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._cards: dict[str, MetricCard] = {}
        for index, (source, title) in enumerate(self._FIELDS):
            card = MetricCard(title)
            layout.addWidget(card, 0, index)
            layout.setColumnStretch(index, 1)
            self._cards[source] = card

    def render(self, snapshot: PortfolioDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
        for source, card in self._cards.items():
            card.set_value(metrics.get(source, "--"))


__all__ = ["PortfolioSummaryStrip"]

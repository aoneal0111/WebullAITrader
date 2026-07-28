from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from app.gui.components.cards import MetricCard
from app.gui.models import PortfolioSnapshot
from app.gui.theme import Spacing


class PortfolioCards(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.SM)
        self.cards = {
            "equity": MetricCard("Equity"),
            "buying_power": MetricCard("Buying Power"),
            "daily_pnl": MetricCard("Daily P&L"),
            "unrealized_pnl": MetricCard("Unrealized P&L"),
            "positions": MetricCard("Positions"),
            "orders": MetricCard("Orders"),
        }
        for index, card in enumerate(self.cards.values()):
            layout.addWidget(card, 0, index)
            layout.setColumnStretch(index, 1)
        self.selected_symbol = self.cards["equity"].note_label

    def render(self, snapshot: PortfolioSnapshot) -> None:
        if not isinstance(snapshot, PortfolioSnapshot):
            raise TypeError("snapshot must be a PortfolioSnapshot")
        focus = (
            "ALL SYMBOLS"
            if snapshot.selected_symbol is None
            else f"FOCUS: {snapshot.selected_symbol}"
        )
        self.cards["equity"].set_value(snapshot.equity, focus)
        self.cards["buying_power"].set_value(
            "—",
            "Awaiting account projection",
        )
        self.cards["daily_pnl"].set_value(snapshot.realized_pnl)
        self.cards["unrealized_pnl"].set_value(
            snapshot.unrealized_pnl
        )
        self.cards["positions"].set_value(
            str(snapshot.position_count)
        )
        self.cards["orders"].set_value(str(snapshot.order_count))

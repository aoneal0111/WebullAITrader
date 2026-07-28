from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QFrame

from app.gui.models import PortfolioSnapshot


class _MetricCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        self.value_label = QLabel("--")
        self.value_label.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)


class PortfolioMetrics(QFrame):
    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._cards = {
            "equity": _MetricCard("EQUITY"),
            "realized": _MetricCard("REALIZED P&L"),
            "unrealized": _MetricCard("UNREALIZED P&L"),
            "drawdown": _MetricCard("DRAWDOWN"),
            "return": _MetricCard("TOTAL RETURN"),
            "win_rate": _MetricCard("WIN RATE"),
        }
        for index, card in enumerate(self._cards.values()):
            layout.addWidget(card, index // 3, index % 3)
        self.selected_symbol = QLabel("ALL SYMBOLS")
        self.selected_symbol.setObjectName("statusBadge")
        layout.addWidget(self.selected_symbol, 2, 0, 1, 3)

    def render(self, snapshot: PortfolioSnapshot) -> None:
        values = {
            "equity": snapshot.equity,
            "realized": snapshot.realized_pnl,
            "unrealized": snapshot.unrealized_pnl,
            "drawdown": snapshot.current_drawdown,
            "return": snapshot.total_return,
            "win_rate": snapshot.win_rate,
        }
        for name, value in values.items():
            self._cards[name].value_label.setText(value)
        self.selected_symbol.setText(
            (
                "ALL SYMBOLS"
                if snapshot.selected_symbol is None
                else f"FOCUS: {snapshot.selected_symbol}"
            )
        )

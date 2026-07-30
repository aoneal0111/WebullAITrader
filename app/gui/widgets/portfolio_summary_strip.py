from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from app.gui.models import PortfolioDashboardSnapshot
from app.gui.widgets.common import MetricCard


class _CompactMetric(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        label = QLabel(title.upper())
        label.setObjectName("metricTitle")
        self._value = QLabel("--")
        self._value.setObjectName("compactMetricValue")
        layout.addWidget(label)
        layout.addWidget(self._value)

    def set_value(self, value: str, note: str | None = None) -> None:
        del note
        self._value.setText(value)

    def set_tone(self, tone: str) -> None:
        self._value.setProperty("tone", tone)
        self._value.style().unpolish(self._value)
        self._value.style().polish(self._value)


class _CompactMetricGroup(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        self.setProperty("emphasis", "compact")
        layout = QGridLayout(self)
        layout.setContentsMargins(13, 8, 13, 8)
        layout.setHorizontalSpacing(18)
        layout.setVerticalSpacing(5)
        self.metrics = {
            "Open Positions": _CompactMetric("Positions"),
            "Working Orders": _CompactMetric("Working Orders"),
            "Realized P/L": _CompactMetric("Realized"),
            "Unrealized P/L": _CompactMetric("Unrealized"),
        }
        for index, metric in enumerate(self.metrics.values()):
            layout.addWidget(metric, index // 2, index % 2)
            layout.setColumnStretch(index % 2, 1)


class PortfolioSummaryStrip(QWidget):
    """Render presenter-formatted portfolio values with visual hierarchy."""

    def __init__(self) -> None:
        super().__init__()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(0)

        net_liquidity = MetricCard(
            "Net Liquidity",
            emphasis="primary",
        )
        total_pnl = MetricCard("Total P&L", emphasis="primary")
        exposure = MetricCard("Exposure", emphasis="medium")
        buying_power = MetricCard("Buying Power", emphasis="medium")
        compact = _CompactMetricGroup()

        layout.addWidget(net_liquidity, 0, 0, 1, 3)
        layout.addWidget(total_pnl, 0, 3, 1, 3)
        layout.addWidget(exposure, 0, 6, 1, 2)
        layout.addWidget(buying_power, 0, 8, 1, 2)
        layout.addWidget(compact, 0, 10, 1, 4)
        for column in range(14):
            layout.setColumnStretch(column, 1)

        self._cards = {
            "Equity": net_liquidity,
            "Total P/L": total_pnl,
            "Gross Exposure": exposure,
            "Buying Power": buying_power,
            **compact.metrics,
        }

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

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.analytics import AnalyticsSnapshot


class AnalyticsPanel(QWidget):
    """Read-only presentation of immutable historical analytics."""

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        self.overview = QWidget()
        overview_form = QFormLayout(self.overview)
        self.overview_values = {
            name: QLabel("--")
            for name in (
                "Status", "Total Trades", "Win Rate",
                "Net Realized PnL", "Maximum Drawdown",
            )
        }
        for name, label in self.overview_values.items():
            overview_form.addRow(name, label)
        self.tabs.addTab(self.overview, "Overview")

        self.performance = self._table(("Metric", "Value"))
        self.risk = self._table(("Metric", "Value"))
        self.symbols = self._table(
            ("Symbol", "Trades", "Win Rate", "Realized PnL")
        )
        self.strategies = self._table(
            ("Dimension", "Value", "Count", "Realized PnL")
        )
        self.time_analysis = self._table(
            ("Dimension", "Period", "Trades", "Wins", "Realized PnL")
        )
        self.tabs.addTab(self.performance, "Performance")
        self.tabs.addTab(self.risk, "Risk")
        self.tabs.addTab(self.symbols, "Symbols")
        self.tabs.addTab(self.strategies, "Strategies")
        self.tabs.addTab(self.time_analysis, "Time Analysis")

    def render(self, snapshot: AnalyticsSnapshot) -> None:
        if not isinstance(snapshot, AnalyticsSnapshot):
            raise TypeError("snapshot must be AnalyticsSnapshot")
        performance = snapshot.performance
        risk = snapshot.risk
        values = {
            "Status": snapshot.status.value,
            "Total Trades": str(performance.total_trades),
            "Win Rate": _percent(performance.win_rate),
            "Net Realized PnL": _money(performance.net_realized_pnl),
            "Maximum Drawdown": _money(risk.maximum_drawdown),
        }
        for name, value in values.items():
            self.overview_values[name].setText(value)

        self._rows(
            self.performance,
            (
                ("Winning Trades", performance.winning_trades),
                ("Losing Trades", performance.losing_trades),
                ("Average Gain", _money(performance.average_gain)),
                ("Average Loss", _money(performance.average_loss)),
                (
                    "Profit Factor",
                    "--" if performance.profit_factor is None
                    else f"{performance.profit_factor:.2f}",
                ),
                ("Expectancy", _money(performance.expectancy)),
                (
                    "Average Holding Duration",
                    _duration(performance.average_holding_duration),
                ),
                ("Largest Winner", _money(performance.largest_winner)),
                ("Largest Loser", _money(performance.largest_loser)),
                ("Gross Profit", _money(performance.gross_profit)),
                ("Gross Loss", _money(performance.gross_loss)),
            ),
        )
        self._rows(
            self.risk,
            (
                ("Peak Equity", _money(risk.peak_equity)),
                (
                    "Recovery Factor",
                    "--" if risk.recovery_factor is None
                    else f"{risk.recovery_factor:.2f}",
                ),
                ("Ulcer Index", _money(risk.ulcer_index)),
                ("Average Exposure", _money(risk.average_exposure)),
                ("Largest Position", _money(risk.largest_position)),
            ),
        )
        self._rows(
            self.symbols,
            tuple(
                (
                    item.symbol,
                    item.performance.total_trades,
                    _percent(item.performance.win_rate),
                    _money(item.performance.net_realized_pnl),
                )
                for item in snapshot.symbols
            ),
        )
        strategy_rows = []
        for dimension, groups in (
            ("Strategy", snapshot.strategy.by_strategy_version),
            ("Decision", snapshot.strategy.by_decision),
            ("Committee", snapshot.strategy.by_committee_outcome),
        ):
            strategy_rows.extend(
                (dimension, key, count, _money(pnl))
                for key, count, pnl in groups
            )
        strategy_rows.extend(
            ("Lifecycle", key, count, "--")
            for key, count in snapshot.strategy.by_lifecycle_phase
        )
        self._rows(self.strategies, tuple(strategy_rows))
        self._rows(
            self.time_analysis,
            tuple(
                (
                    item.dimension,
                    item.period,
                    item.total_trades,
                    item.winning_trades,
                    _money(item.realized_pnl),
                )
                for item in snapshot.time_metrics
            ),
        )

    @staticmethod
    def _table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        return table

    @staticmethod
    def _rows(table: QTableWidget, rows: tuple[tuple, ...]) -> None:
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))


def _money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.2f}%"


def _duration(value: timedelta | None) -> str:
    return "--" if value is None else str(value)

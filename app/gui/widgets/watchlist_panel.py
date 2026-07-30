from __future__ import annotations

from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import WatchlistSnapshot


class WatchlistPanel(QWidget):
    """Render a prepared immutable watchlist snapshot."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels(
            (
                "Symbol",
                "Price",
                "Change",
                "Change %",
                "Bid",
                "Ask",
                "Volume",
                "Market",
                "Updated",
                "State",
            )
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self._table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            values = (
                f"● {row.symbol}" if row.selected else row.symbol,
                row.latest_price,
                row.change,
                row.change_percent,
                row.bid,
                row.ask,
                row.volume,
                row.market_status,
                row.last_update,
                row.stale,
            )
            for column_index, value in enumerate(values):
                self._table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )

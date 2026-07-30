from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import WatchlistSnapshot


class WatchlistPanel(QWidget):
    """Render a prepared immutable watchlist snapshot."""

    sort_requested = Signal(str)

    _SORT_FIELDS = {
        0: "symbol",
        1: "latest_price",
        3: "change_percent",
        6: "volume",
        7: "market_status",
        9: "stale",
    }

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
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().sectionClicked.connect(
            self._request_sort
        )
        layout.addWidget(self._table)

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self._table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            values = (
                f"\u25cf {row.symbol}" if row.selected else row.symbol,
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
        columns = {
            field: column
            for column, field in self._SORT_FIELDS.items()
        }
        if snapshot.sort_field in columns:
            self._table.horizontalHeader().setSortIndicator(
                columns[snapshot.sort_field],
                (
                    Qt.SortOrder.DescendingOrder
                    if snapshot.descending
                    else Qt.SortOrder.AscendingOrder
                ),
            )

    def _request_sort(self, column: int) -> None:
        field = self._SORT_FIELDS.get(column)
        if field is not None:
            self.sort_requested.emit(field)

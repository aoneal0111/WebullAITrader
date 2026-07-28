from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import PositionsSnapshot


class PositionsPanel(QWidget):
    selection_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ("Symbol", "Qty", "Avg Price", "P/L")
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self._table.cellClicked.connect(self._request_selection)

        layout.addWidget(self._table)

    def render(self, snapshot: PositionsSnapshot) -> None:
        rows = snapshot.rows or (
            ("--", "--", "--", "--"),
            ("--", "--", "--", "--"),
            ("--", "--", "--", "--"),
        )

        self._table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            symbol = (
                snapshot.symbols[row_index]
                if row_index < len(snapshot.symbols)
                else ""
            )
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, symbol)
                if symbol and symbol == snapshot.selected_symbol:
                    item.setBackground(QColor("#243b53"))
                self._table.setItem(row_index, column_index, item)
            if symbol and symbol == snapshot.selected_symbol:
                self._table.scrollToItem(self._table.item(row_index, 0))

    def _request_selection(self, row: int, column: int) -> None:
        item = self._table.item(row, column)
        if item is None:
            return
        symbol = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(symbol, str) and symbol:
            self.selection_requested.emit(symbol)

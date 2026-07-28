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

from app.gui.models import OrdersSnapshot


class OrdersPanel(QWidget):
    selection_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(
            ("Order", "Status", "Updated")
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

    def render(self, snapshot: OrdersSnapshot) -> None:
        rows = snapshot.rows or (
            ("--", "--", "--"),
            ("--", "--", "--"),
            ("--", "--", "--"),
        )

        self._table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            symbol = (
                snapshot.symbols[row_index]
                if row_index < len(snapshot.symbols)
                else ""
            )
            order_id = (
                snapshot.order_ids[row_index]
                if row_index < len(snapshot.order_ids)
                else ""
            )
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    (symbol, order_id),
                )
                if order_id and order_id == snapshot.selected_order:
                    item.setBackground(QColor("#243b53"))
                self._table.setItem(row_index, column_index, item)
            if order_id and order_id == snapshot.selected_order:
                self._table.scrollToItem(self._table.item(row_index, 0))

    def _request_selection(self, row: int, column: int) -> None:
        item = self._table.item(row, column)
        if item is None:
            return
        selection = item.data(Qt.ItemDataRole.UserRole)
        if (
            isinstance(selection, tuple)
            and len(selection) == 2
            and all(isinstance(value, str) for value in selection)
            and selection[0]
            and selection[1]
        ):
            self.selection_requested.emit(selection[0], selection[1])

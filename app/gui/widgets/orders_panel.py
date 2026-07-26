from __future__ import annotations

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.gui.models import OrdersSnapshot


class OrdersPanel(QWidget):
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

        layout.addWidget(self._table)

    def render(self, snapshot: OrdersSnapshot) -> None:
        rows = snapshot.rows or (
            ("--", "--", "--"),
            ("--", "--", "--"),
            ("--", "--", "--"),
        )

        self._table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                self._table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )

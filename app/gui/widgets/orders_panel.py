from __future__ import annotations

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class OrdersPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(3, 3)
        self._table.setHorizontalHeaderLabels(
            ("Order", "Status", "Updated")
        )

        for row in range(3):
            for column in range(3):
                self._table.setItem(
                    row,
                    column,
                    QTableWidgetItem("--"),
                )

        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEnabled(False)

        layout.addWidget(self._table)

from __future__ import annotations

from PySide6.QtWidgets import QTableWidgetItem, QVBoxLayout, QWidget

from app.gui.models import PositionsSnapshot
from app.gui.widgets.data_table import StyledDataTable


class PositionsPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = StyledDataTable(
            ("Symbol", "Qty", "Avg Price", "P/L")
        )

        layout.addWidget(self._table)

    def render(self, snapshot: PositionsSnapshot) -> None:
        rows = snapshot.rows or (
            ("--", "--", "--", "--"),
            ("--", "--", "--", "--"),
            ("--", "--", "--", "--"),
        )

        self._table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                self._table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem, QVBoxLayout, QWidget

from app.gui.design.tokens import Colors
from app.gui.models import PositionsSnapshot
from app.gui.widgets.data_table import StyledDataTable


class PositionsPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._table = StyledDataTable(
            ("Symbol", "Side", "Size", "Average Price", "Mark", "PnL", "PnL %")
        )
        self._table.set_empty_state(
            "No positions",
            "Filled orders will appear here.",
            icon="\u25ce",
        )

        layout.addWidget(self._table)

    def render(self, snapshot: PositionsSnapshot) -> None:
        rows = snapshot.rows

        self._table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                if column_index >= 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if column_index == 1:
                    item.setForeground(
                        QBrush(QColor(
                            Colors.SUCCESS if value == "LONG" else
                            Colors.DANGER if value == "SHORT" else
                            Colors.TEXT_MUTED
                        ))
                    )
                if column_index in (5, 6):
                    item.setForeground(
                        QBrush(QColor(_financial_color(value)))
                    )
                self._table.setItem(row_index, column_index, item)


def _financial_color(value: str) -> str:
    if value.startswith("+"):
        return Colors.SUCCESS
    if value.startswith("-"):
        return Colors.DANGER
    return Colors.TEXT_MUTED

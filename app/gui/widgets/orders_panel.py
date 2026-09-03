from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem, QVBoxLayout, QWidget

from app.gui.design.tokens import Colors
from app.gui.models import OrdersSnapshot
from app.gui.widgets.data_table import StyledDataTable


class OrdersPanel(QWidget):
    """Compact read-only active/recent order supervision table."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = StyledDataTable(
            (
                "Symbol", "Side", "Type", "Qty", "Filled", "Remaining",
                "Limit", "Stop", "Avg Fill", "Status",
            )
        )
        self._table.set_empty_state(
            "No active orders",
            "Working and recent orders will appear here.",
            icon="\u2637",
        )
        layout.addWidget(self._table)

    def render(self, snapshot: OrdersSnapshot) -> None:
        self._table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            protective = row_index in snapshot.protective_rows
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                if column_index == 1:
                    item.setForeground(QBrush(QColor(
                        Colors.SUCCESS if value.upper() in {"BUY", "COVER"}
                        else Colors.DANGER if value.upper() in {"SELL", "SHORT"}
                        else Colors.TEXT_MUTED
                    )))
                if column_index == 9:
                    item.setForeground(QBrush(QColor(_status_color(value))))
                if column_index in {3, 4, 5, 6, 7, 8}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if protective:
                    item.setBackground(QBrush(QColor(Colors.DANGER_SOFT)))
                    item.setToolTip("Protective exit order")
                self._table.setItem(row_index, column_index, item)


def _status_color(value: str) -> str:
    normalized = value.upper()
    if normalized in {"FILLED", "ACCEPTED", "WORKING"}:
        return Colors.SUCCESS
    if normalized in {"REJECTED", "CANCELLED", "FAILED"}:
        return Colors.DANGER
    if normalized in {"PENDING", "SUBMITTED", "PARTIALLY_FILLED"}:
        return Colors.WARNING
    return Colors.TEXT_MUTED

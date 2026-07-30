from __future__ import annotations

from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import DecisionsSnapshot


class DecisionsPanel(QWidget):
    """Render an already-prepared immutable decisions snapshot."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels(
            (
                "Time",
                "Strategy",
                "Symbol",
                "Action",
                "Confidence",
                "Reasoning",
                "Risk",
                "Qty",
                "Order",
                "Outcome",
            )
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    def render(self, snapshot: DecisionsSnapshot) -> None:
        self._table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            values = (
                row.timestamp.astimezone().strftime("%H:%M:%S"),
                row.strategy,
                row.symbol,
                row.action,
                row.confidence,
                row.reasoning,
                row.risk,
                row.quantity,
                row.order_id,
                row.outcome,
            )
            for column_index, value in enumerate(values):
                self._table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )

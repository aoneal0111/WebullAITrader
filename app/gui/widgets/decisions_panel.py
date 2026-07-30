from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import DecisionsSnapshot
from app.gui.widgets.data_table import StyledDataTable


class DecisionsPanel(QWidget):
    """Render immutable decision rows and the selected decision inspector."""

    decision_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self._table = StyledDataTable(
            (
                "Time",
                "Strategy",
                "Symbol",
                "Action",
                "Confidence",
                "Outcome",
            )
        )
        inspector = QWidget()
        details = QFormLayout(inspector)
        self._title = QLabel("No decision selected")
        self._confidence = QLabel("--")
        self._reasoning = QLabel("--")
        self._reasoning.setWordWrap(True)
        self._risk = QLabel("--")
        self._quantity = QLabel("--")
        self._order = QLabel("--")
        self._lifecycle = QLabel("--")
        self._lifecycle.setWordWrap(True)
        self._outcome = QLabel("--")
        details.addRow("Decision", self._title)
        details.addRow("Confidence", self._confidence)
        details.addRow("Reasoning", self._reasoning)
        details.addRow("Risk", self._risk)
        details.addRow("Requested quantity", self._quantity)
        details.addRow("Resulting order", self._order)
        details.addRow("Lifecycle", self._lifecycle)
        details.addRow("Execution outcome", self._outcome)

        splitter.addWidget(self._table)
        splitter.addWidget(inspector)
        layout.addWidget(splitter)
        self._table.itemSelectionChanged.connect(self._emit_selection)

    def render(self, snapshot: DecisionsSnapshot) -> None:
        self._table.blockSignals(True)
        self._table.setRowCount(len(snapshot.rows))
        selected_row = None
        for row_index, row in enumerate(snapshot.rows):
            values = (
                row.timestamp.astimezone().strftime("%H:%M:%S"),
                row.strategy,
                row.symbol,
                row.action,
                row.confidence,
                row.outcome,
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row.decision_id)
                self._table.setItem(row_index, column_index, item)
            if (
                snapshot.selected is not None
                and row.decision_id == snapshot.selected.decision_id
            ):
                selected_row = row_index
        if selected_row is not None:
            self._table.selectRow(selected_row)
        self._table.blockSignals(False)

        detail = snapshot.selected
        self._title.setText(detail.title if detail else "No decision selected")
        self._confidence.setText(detail.confidence if detail else "--")
        self._reasoning.setText(detail.reasoning if detail else "--")
        self._risk.setText(detail.risk if detail else "--")
        self._quantity.setText(detail.requested_quantity if detail else "--")
        self._order.setText(detail.resulting_order_id if detail else "--")
        self._lifecycle.setText(
            "  \u2192  ".join(detail.lifecycle) if detail else "--"
        )
        self._outcome.setText(
            detail.execution_outcome if detail else "--"
        )

    def _emit_selection(self) -> None:
        selected = self._table.selectedItems()
        if selected:
            decision_id = selected[0].data(Qt.ItemDataRole.UserRole)
            if decision_id:
                self.decision_selected.emit(decision_id)

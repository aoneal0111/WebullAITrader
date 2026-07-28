from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import DecisionCenterSnapshot


class DecisionCenter(QWidget):
    """Read-only view of the latest autonomous decision projection."""

    selection_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.summary = QLabel()
        self.summary.setObjectName("muted")
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Symbol", "Decision", "Confidence", "Score", "Rationale", "Time")
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.cellClicked.connect(self._request_selection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1, 2, 3, 5):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        self.render(DecisionCenterSnapshot.initial())

    def render(self, snapshot: DecisionCenterSnapshot) -> None:
        if not isinstance(snapshot, DecisionCenterSnapshot):
            raise TypeError("snapshot must be a DecisionCenterSnapshot")
        self.summary.setText(f"{snapshot.cycle}  |  {snapshot.updated_at}")
        self.table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            values = (
                row.symbol,
                row.action,
                row.confidence,
                row.score,
                row.rationale,
                row.decided_at,
            )
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    (row.symbol, row.selection_id),
                )
                if (
                    row.selection_id
                    and row.selection_id == snapshot.selected_decision
                ):
                    item.setBackground(QColor("#243b53"))
                self.table.setItem(row_index, column_index, item)
            if (
                row.selection_id
                and row.selection_id == snapshot.selected_decision
            ):
                self.table.scrollToItem(self.table.item(row_index, 0))

    def _request_selection(self, row: int, column: int) -> None:
        item = self.table.item(row, column)
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

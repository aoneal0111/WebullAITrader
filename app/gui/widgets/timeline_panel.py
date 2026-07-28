from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import TimelineSnapshot


class TimelinePanel(QWidget):
    """Read-only newest-first table of immutable timeline rows."""

    selection_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ("Time", "Category", "Severity", "Summary")
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
        for column in (0, 1, 2):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

    def render(self, snapshot: TimelineSnapshot) -> None:
        if not isinstance(snapshot, TimelineSnapshot):
            raise TypeError("snapshot must be a TimelineSnapshot")
        self.table.setRowCount(len(snapshot.rows))
        for row_index, row in enumerate(snapshot.rows):
            for column_index, value in enumerate(
                (
                    row.time,
                    row.category,
                    row.severity,
                    row.summary,
                )
            ):
                item = QTableWidgetItem(value)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    (row.symbol or "", row.selection_id),
                )
                if (
                    row.selection_id
                    and row.selection_id == snapshot.selected_entry
                ):
                    item.setBackground(QColor("#243b53"))
                self.table.setItem(row_index, column_index, item)
            if (
                row.selection_id
                and row.selection_id == snapshot.selected_entry
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
            and selection[1]
        ):
            self.selection_requested.emit(selection[0], selection[1])

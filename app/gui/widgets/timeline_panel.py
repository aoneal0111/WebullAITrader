from __future__ import annotations

from PySide6.QtCore import Qt
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
                self.table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(value),
                )

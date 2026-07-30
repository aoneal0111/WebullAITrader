from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
)

from app.gui.design.tokens import Dimensions


class StyledDataTable(QTableWidget):
    """Shared dense, read-only table primitive for operator workspaces."""

    def __init__(self, columns: Sequence[str]) -> None:
        super().__init__(0, len(columns))
        self.setHorizontalHeaderLabels(tuple(columns))
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.horizontalHeader().setSectionResizeMode(
            len(columns) - 1,
            QHeaderView.ResizeMode.Stretch,
        )
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(
            Dimensions.TABLE_ROW_HEIGHT
        )
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setShowGrid(False)


__all__ = ["StyledDataTable"]

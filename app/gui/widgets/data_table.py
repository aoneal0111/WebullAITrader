from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
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
        self._empty_state = QLabel(self.viewport())
        self._empty_state.setObjectName("emptyState")
        self._empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state.setWordWrap(True)
        self._empty_state.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._empty_state.hide()

    def set_empty_state(
        self,
        title: str,
        detail: str,
        *,
        icon: str = "\u25cb",
    ) -> None:
        self._empty_state.setText(
            "\n".join(part for part in (icon, title, detail) if part)
        )
        self._sync_empty_state()

    def setRowCount(self, rows: int) -> None:
        super().setRowCount(rows)
        if hasattr(self, "_empty_state"):
            self._sync_empty_state()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_empty_state"):
            self._empty_state.setGeometry(self.viewport().rect())

    def _sync_empty_state(self) -> None:
        self._empty_state.setGeometry(self.viewport().rect())
        self._empty_state.setVisible(
            self.rowCount() == 0 and bool(self._empty_state.text())
        )


__all__ = ["StyledDataTable"]

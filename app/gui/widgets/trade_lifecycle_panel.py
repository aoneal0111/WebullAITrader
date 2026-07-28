from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import LifecycleExplorerSnapshot


class TradeLifecyclePanel(QWidget):
    """Read-only expandable view of immutable symbol trade histories."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(
            (
                "Symbol / Time",
                "Status / Phase",
                "Opened",
                "Closed",
                "PnL",
                "Details",
            )
        )
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        header = self.tree.header()
        for column in range(5):
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)

    def render(self, snapshot: LifecycleExplorerSnapshot) -> None:
        if not isinstance(snapshot, LifecycleExplorerSnapshot):
            raise TypeError(
                "snapshot must be a LifecycleExplorerSnapshot"
            )
        self.tree.clear()
        for row in snapshot.rows:
            parent = QTreeWidgetItem(
                (
                    row.symbol,
                    row.status,
                    row.opened,
                    row.closed,
                    row.realized_pnl,
                    "",
                )
            )
            self.tree.addTopLevelItem(parent)
            for entry in row.entries:
                parent.addChild(
                    QTreeWidgetItem(
                        (
                            entry.time,
                            entry.phase,
                            "",
                            "",
                            "",
                            entry.summary,
                        )
                    )
                )
            parent.setExpanded(row.symbol == snapshot.selected_symbol)

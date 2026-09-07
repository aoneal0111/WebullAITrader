from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QTableWidgetItem, QVBoxLayout, QWidget

from app.gui.design.tokens import Colors
from app.gui.models import OrdersSnapshot
from app.gui.widgets.data_table import StyledDataTable
from app.operations_core.projection_authority import ACTIVE_ORDER_STATUSES


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


class MissionControlOrdersPanel(QWidget):
    """Bounded recent-order view that never elides an active order."""

    active_orders_changed = Signal(bool)
    RECENT_TERMINAL_LIMIT = 5

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._table = StyledDataTable(
            ("Symbol", "Side", "Qty", "Limit / Stop / Fill", "Status", "Updated")
        )
        self._table.setObjectName("missionControlRecentOrdersTable")
        self._table.set_empty_state(
            "No working or recent orders",
            "Exposure-affecting working orders will always appear here.",
            icon="☷",
        )
        self._table.setSelectionMode(self._table.SelectionMode.NoSelection)
        layout.addWidget(self._table)
        self._active_order_count = 0

    def render(self, snapshot: OrdersSnapshot) -> None:
        indexed = tuple(enumerate(snapshot.rows))
        active = tuple(
            item for item in indexed
            if _normalized_status(item[1][9]) in ACTIVE_ORDER_STATUSES
        )
        terminal = tuple(
            item for item in indexed
            if _normalized_status(item[1][9]) not in ACTIVE_ORDER_STATUSES
        )[: self.RECENT_TERMINAL_LIMIT]
        if len(active) != self._active_order_count:
            self._active_order_count = len(active)
            self.active_orders_changed.emit(bool(active))
        visible = (*active, *terminal)
        self._table.setRowCount(len(visible))
        for row_index, (source_index, row) in enumerate(visible):
            values = (
                row[0],
                row[1],
                row[3],
                _price_summary(row),
                row[9],
                (
                    snapshot.updated_at[source_index]
                    if source_index < len(snapshot.updated_at)
                    else "—"
                ),
            )
            protective = source_index in snapshot.protective_rows
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index == 1:
                    item.setForeground(QBrush(QColor(
                        Colors.SUCCESS if value.upper() in {"BUY", "COVER"}
                        else Colors.DANGER if value.upper() in {"SELL", "SHORT"}
                        else Colors.TEXT_MUTED
                    )))
                if column_index == 4:
                    item.setForeground(QBrush(QColor(_status_color(value))))
                if column_index in {2, 3, 5}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                if protective:
                    item.setBackground(QBrush(QColor(Colors.DANGER_SOFT)))
                    item.setToolTip("Protective exit order")
                self._table.setItem(row_index, column_index, item)


def _normalized_status(value: str) -> str:
    return value.strip().upper().replace(" ", "_")


def _price_summary(row: tuple[str, ...]) -> str:
    labels = (("LMT", row[6]), ("STOP", row[7]), ("FILL", row[8]))
    material = tuple(
        f"{label} {value}"
        for label, value in labels
        if value not in {"—", "--", "Not available"}
    )
    return " · ".join(material) if material else row[2]


def _status_color(value: str) -> str:
    normalized = value.upper()
    if normalized in {"FILLED", "ACCEPTED", "WORKING"}:
        return Colors.SUCCESS
    if normalized in {"REJECTED", "CANCELLED", "FAILED"}:
        return Colors.DANGER
    if normalized in {"PENDING", "SUBMITTED", "PARTIALLY_FILLED"}:
        return Colors.WARNING
    return Colors.TEXT_MUTED


__all__ = ["MissionControlOrdersPanel", "OrdersPanel"]

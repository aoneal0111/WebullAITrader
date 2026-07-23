from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.operations_core import ApplicationState


class OrdersPage(QWidget):
    """Read-only order monitoring page."""

    def __init__(self) -> None:
        super().__init__()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        header = QHBoxLayout()

        heading = QVBoxLayout()
        heading.setSpacing(3)

        title = QLabel("Orders")
        title.setObjectName("pageTitle")

        subtitle = QLabel(
            "Monitor paper orders, execution status, and validation criteria."
        )
        subtitle.setObjectName("muted")

        heading.addWidget(title)
        heading.addWidget(subtitle)

        self._runtime_status = QLabel("PAPER - STOPPED")
        self._runtime_status.setObjectName("statusPill")
        self._runtime_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header.addLayout(heading)
        header.addStretch()
        header.addWidget(self._runtime_status)

        root.addLayout(header)

        self._orders_table = QTableWidget(0, 8)
        self._orders_table.setHorizontalHeaderLabels(
            (
                "Symbol",
                "Side",
                "Type",
                "Quantity",
                "Remaining",
                "Status",
                "Price",
                "Submitted",
            )
        )
        self._orders_table.setAlternatingRowColors(True)
        self._orders_table.setShowGrid(False)
        self._orders_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._orders_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._orders_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._orders_table.verticalHeader().setVisible(False)
        self._orders_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        orders_panel = QFrame()
        orders_panel.setObjectName("contentPanel")

        orders_layout = QVBoxLayout(orders_panel)
        orders_layout.setContentsMargins(14, 14, 14, 14)
        orders_layout.setSpacing(10)

        orders_title = QLabel("ACTIVE ORDERS")
        orders_title.setObjectName("sectionTitle")

        orders_layout.addWidget(orders_title)
        orders_layout.addWidget(self._orders_table)

        root.addWidget(orders_panel, 3)

        lower = QGridLayout()
        lower.setSpacing(12)

        details_panel = QFrame()
        details_panel.setObjectName("contentPanel")

        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(14, 14, 14, 14)
        details_layout.setSpacing(9)

        details_title = QLabel("SELECTED ORDER")
        details_title.setObjectName("sectionTitle")
        details_layout.addWidget(details_title)

        self._broker_order_id = self._detail_row(
            details_layout,
            "Broker Order ID",
        )
        self._client_order_id = self._detail_row(
            details_layout,
            "Client Order ID",
        )
        self._submitted_at = self._detail_row(
            details_layout,
            "Submitted",
        )
        self._average_fill_price = self._detail_row(
            details_layout,
            "Average Fill Price",
        )
        self._remaining_quantity = self._detail_row(
            details_layout,
            "Remaining Quantity",
        )

        details_layout.addStretch()

        criteria_panel = QFrame()
        criteria_panel.setObjectName("contentPanel")

        criteria_layout = QVBoxLayout(criteria_panel)
        criteria_layout.setContentsMargins(14, 14, 14, 14)
        criteria_layout.setSpacing(9)

        criteria_title = QLabel("ORDER CRITERIA")
        criteria_title.setObjectName("sectionTitle")
        criteria_layout.addWidget(criteria_title)

        self._session_criteria = QLabel("Session: Waiting")
        self._gateway_criteria = QLabel("Gateway: Waiting")
        self._tracking_criteria = QLabel("Order tracking: No order selected")

        for label in (
            self._session_criteria,
            self._gateway_criteria,
            self._tracking_criteria,
        ):
            label.setObjectName("monitorValue")
            label.setWordWrap(True)
            criteria_layout.addWidget(label)

        criteria_layout.addStretch()

        lower.addWidget(details_panel, 0, 0)
        lower.addWidget(criteria_panel, 0, 1)
        lower.setColumnStretch(0, 1)
        lower.setColumnStretch(1, 1)

        root.addLayout(lower, 2)

        self._show_empty_state()

    @staticmethod
    def _detail_row(
        layout: QVBoxLayout,
        title: str,
    ) -> QLabel:
        row = QHBoxLayout()

        key = QLabel(title)
        key.setObjectName("monitorKey")

        value = QLabel("--")
        value.setObjectName("monitorValue")
        value.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        row.addWidget(key)
        row.addStretch()
        row.addWidget(value)

        layout.addLayout(row)
        return value

    def _show_empty_state(self) -> None:
        self._orders_table.setRowCount(1)
        self._orders_table.clearSpans()

        item = QTableWidgetItem(
            "No active paper orders are available."
        )
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        self._orders_table.setItem(0, 0, item)
        self._orders_table.setSpan(0, 0, 1, 8)

    def render(self, state: ApplicationState) -> None:
        runtime = state.runtime

        self._runtime_status.setText(
            f"{runtime.environment} - {runtime.phase.value}"
        )

        self._session_criteria.setText(
            f"Session: {runtime.phase.value.title()}"
        )
        self._gateway_criteria.setText(
            f"Gateway: {runtime.broker_status}"
        )

        if self._orders_table.rowCount() == 0:
            self._show_empty_state()

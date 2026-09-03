from __future__ import annotations

from decimal import Decimal
from collections.abc import Mapping
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from app.gui.formatters.prices import format_price

from app.open_orders.models import (
    OpenOrderSnapshot,
    OpenOrdersCriteriaResult,
    OpenOrdersResult,
)
from app.operations_core import ApplicationState
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot
from app.services import OrderCommandFactory, OrderEntryCommand, TradingService
from app.gui.widgets.order_entry_panel import OrderEntryPanel


class OrdersPage(QWidget):
    """Read-only presentation surface for immutable open-order snapshots."""

    ALL_STATUSES = "ALL STATUSES"

    def __init__(
        self,
        trading_service: TradingService | None = None,
        order_command_factory: OrderCommandFactory | None = None,
    ) -> None:
        super().__init__()

        if (trading_service is None) != (order_command_factory is None):
            raise ValueError(
                "trading_service and order_command_factory must be provided together"
            )

        self._trading_service = trading_service
        self._order_command_factory = order_command_factory

        self._orders: tuple[OpenOrderSnapshot, ...] = ()
        self._visible_orders: tuple[OpenOrderSnapshot, ...] = ()
        self._projected_orders: tuple[OrderReadModel, ...] | None = None
        self._visible_projected_orders: tuple[OrderReadModel, ...] = ()
        self._criteria: tuple[OpenOrdersCriteriaResult, ...] = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        header = QHBoxLayout()

        heading = QVBoxLayout()
        heading.setSpacing(3)

        title = QLabel("Orders")
        title.setObjectName("pageTitle")

        subtitle = QLabel(
            "Supervise authoritative working, filled, cancelled, and rejected orders."
        )
        subtitle.setObjectName("mutedText")

        heading.addWidget(title)
        heading.addWidget(subtitle)

        self._status_filter = QComboBox()
        self._status_filter.setObjectName("ordersFilter")
        self._status_filter.addItem(self.ALL_STATUSES)
        self._status_filter.currentTextChanged.connect(
            self._apply_status_filter
        )

        self._runtime_status = QLabel("PAPER - STOPPED")
        self._runtime_status.setObjectName("statusPill")
        self._runtime_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header.addLayout(heading)
        header.addStretch()
        header.addWidget(self._status_filter)
        header.addWidget(self._runtime_status)

        root.addLayout(header)

        self.order_entry_panel = OrderEntryPanel()
        self.order_entry_panel.setParent(self)
        self.order_entry_panel.set_execution_enabled(
            self._trading_service is not None
        )
        self.order_entry_panel.order_validated.connect(
            self._place_validated_order
        )
        # Manual entry remains available to explicitly privileged recovery
        # tooling through this compatibility object, but is deliberately not
        # mounted in the normal autonomous workstation.
        self.order_entry_panel.hide()
        orders_panel = QFrame()
        orders_panel.setObjectName("contentPanel")

        orders_layout = QVBoxLayout(orders_panel)
        orders_layout.setContentsMargins(14, 14, 14, 14)
        orders_layout.setSpacing(10)

        orders_header = QHBoxLayout()

        orders_title = QLabel("ORDER SUPERVISION")
        orders_title.setObjectName("sectionTitle")

        self._order_count = QLabel("0 orders")
        self._order_count.setObjectName("mutedText")

        orders_header.addWidget(orders_title)
        orders_header.addStretch()
        orders_header.addWidget(self._order_count)

        self._orders_table = QTableWidget(0, 12)
        self._orders_table.setHorizontalHeaderLabels(
            (
                "SYMBOL",
                "SIDE",
                "QTY",
                "FILLED",
                "REMAINING",
                "TYPE",
                "LIMIT",
                "STOP",
                "AVG FILL",
                "STATUS",
                "SUBMITTED",
                "UPDATED",
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
        self._orders_table.setSortingEnabled(True)
        self._orders_table.verticalHeader().setVisible(False)

        table_header = self._orders_table.horizontalHeader()
        table_header.setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table_header.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )

        self._orders_table.itemSelectionChanged.connect(
            self._render_selected_order
        )

        orders_layout.addLayout(orders_header)
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
        detail_groups = QHBoxLayout()
        detail_groups.setSpacing(20)
        identity_layout = QVBoxLayout()
        identity_layout.setSpacing(4)
        identity_title = QLabel("IDENTITY / LIFECYCLE")
        identity_title.setObjectName("metricTitle")
        identity_layout.addWidget(identity_title)
        self._broker_order_id = self._detail_row(
            identity_layout,
            "Broker Order ID",
        )
        self._client_order_id = self._detail_row(
            identity_layout,
            "Client Order ID",
        )
        self._account_id = self._detail_row(
            identity_layout,
            "Account",
        )
        self._lifecycle_id = self._detail_row(
            identity_layout,
            "Lifecycle",
        )
        self._execution_source = self._detail_row(
            identity_layout,
            "Source / Reason",
        )
        self._submitted_at = self._detail_row(
            identity_layout,
            "Submitted",
        )
        self._updated_at = self._detail_row(identity_layout, "Updated")

        execution_layout = QVBoxLayout()
        execution_layout.setSpacing(4)
        execution_title = QLabel("EXECUTION")
        execution_title.setObjectName("metricTitle")
        execution_layout.addWidget(execution_title)
        self._side = self._detail_row(execution_layout, "Side")
        self._order_type = self._detail_row(execution_layout, "Type")
        self._requested_quantity = self._detail_row(
            execution_layout, "Requested Quantity"
        )
        self._filled_quantity = self._detail_row(execution_layout, "Filled")
        self._remaining_quantity = self._detail_row(
            execution_layout,
            "Remaining",
        )
        self._limit_price = self._detail_row(
            execution_layout,
            "Limit",
        )
        self._stop_price = self._detail_row(
            execution_layout,
            "Stop",
        )
        self._average_fill_price = self._detail_row(
            execution_layout,
            "Average Fill",
        )
        self._detail_status = self._detail_row(execution_layout, "Status")
        identity_layout.addStretch()
        execution_layout.addStretch()
        detail_groups.addLayout(identity_layout, 1)
        detail_groups.addLayout(execution_layout, 1)
        details_layout.addLayout(detail_groups)

        criteria_panel = QFrame()
        criteria_panel.setObjectName("contentPanel")

        criteria_layout = QVBoxLayout(criteria_panel)
        criteria_layout.setContentsMargins(14, 14, 14, 14)
        criteria_layout.setSpacing(9)

        criteria_title = QLabel("ORDER CRITERIA")
        criteria_title.setObjectName("sectionTitle")
        criteria_layout.addWidget(criteria_title)

        self._criteria_summary = QLabel(
            "No open-orders result has been received."
        )
        self._criteria_summary.setObjectName("monitorValue")
        self._criteria_summary.setWordWrap(True)

        self._criteria_detail = QLabel(
            "Criteria checks will appear after an open-orders query."
        )
        self._criteria_detail.setObjectName("mutedText")
        self._criteria_detail.setWordWrap(True)
        self._criteria_detail.setAlignment(
            Qt.AlignmentFlag.AlignTop
            | Qt.AlignmentFlag.AlignLeft
        )

        criteria_layout.addWidget(self._criteria_summary)
        criteria_layout.addWidget(self._criteria_detail, 1)

        lower.addWidget(details_panel, 0, 0)
        lower.addWidget(criteria_panel, 0, 1)
        lower.setColumnStretch(0, 3)
        lower.setColumnStretch(1, 1)

        root.addLayout(lower, 2)

        self._show_empty_state()
        self._clear_selected_order()

    def _place_validated_order(self, payload: object) -> None:
        """Convert validated presentation input and place a paper order."""

        if self._trading_service is None or self._order_command_factory is None:
            self.order_entry_panel.show_submission_error(
                "Paper trading service is unavailable."
            )
            return

        if not isinstance(payload, Mapping):
            self.order_entry_panel.show_submission_error(
                "Validated order payload is invalid."
            )
            return

        try:
            command = OrderEntryCommand(
                symbol=str(payload["symbol"]),
                side=str(payload["side"]),
                quantity=Decimal(str(payload["quantity"])),
                order_type=str(payload["order_type"]),
                limit_price=payload.get("limit_price"),
                stop_price=payload.get("stop_price"),
                time_in_force=str(payload["time_in_force"]),
            )
            request = self._order_command_factory.create_placement_request(
                command
            )
            result = self._trading_service.place_order(request)
        except Exception as exc:
            self.order_entry_panel.show_submission_error(str(exc))
            return

        if result.success:
            self.order_entry_panel.show_submission_success(
                broker_order_id=result.broker_order_id,
                message=result.gateway_message,
            )
        else:
            self.order_entry_panel.show_submission_error(
                result.gateway_message
            )

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
        value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        value.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        row.addWidget(key)
        row.addStretch()
        row.addWidget(value)

        layout.addLayout(row)
        return value

    @staticmethod
    def _decimal_text(value: Decimal | None) -> str:
        if value is None:
            return "--"

        normalized = format(value, "f")
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")

        return normalized

    @classmethod
    def _price_text(cls, order: OpenOrderSnapshot) -> str:
        if order.limit_price is not None and order.stop_price is not None:
            return (
                f"L {format_price(order.limit_price)} / "
                f"S {format_price(order.stop_price)}"
            )

        if order.limit_price is not None:
            return format_price(order.limit_price)

        if order.stop_price is not None:
            return format_price(order.stop_price)

        return "MARKET"

    @staticmethod
    def _status_color(status: str) -> QColor:
        if status == "PARTIALLY_FILLED":
            return QColor("#f1c76d")

        if status in {
            "ACCEPTED",
            "SUBMITTED",
            "REPLACED",
        }:
            return QColor("#70d7a0")

        if status == "UNKNOWN":
            return QColor("#ff7d8a")

        return QColor("#78a9ff")

    def render(self, state: ApplicationState) -> None:
        """Render runtime information without mutating order snapshots."""

        runtime = state.runtime

        self._runtime_status.setText(
            f"{runtime.environment} - {runtime.phase.value}".upper()
        )
        self._runtime_status.setProperty(
            "state",
            "good" if runtime.phase.value == "running" else "neutral",
        )
        self._runtime_status.style().unpolish(self._runtime_status)
        self._runtime_status.style().polish(self._runtime_status)

    def render_orders(
        self,
        orders: Sequence[OpenOrderSnapshot],
    ) -> None:
        """Render an immutable copy of open-order snapshots."""

        normalized = tuple(orders)

        if any(
            not isinstance(order, OpenOrderSnapshot)
            for order in normalized
        ):
            raise TypeError(
                "orders must contain only OpenOrderSnapshot instances"
            )

        self._orders = normalized
        self._projected_orders = None
        self._rebuild_status_filter()
        self._apply_status_filter(
            self._status_filter.currentText()
        )

    def render_result(self, result: OpenOrdersResult) -> None:
        """Render a complete open-orders service result."""

        if not isinstance(result, OpenOrdersResult):
            raise TypeError("result must be OpenOrdersResult")

        self._criteria = result.criteria_results
        self._criteria_summary.setText(
            f"Decision: {result.decision.value} | "
            f"Account: {result.account_id}"
        )

        if self._criteria:
            lines = []
            for criterion in self._criteria:
                marker = "PASS" if criterion.passed else "FAIL"
                lines.append(
                    f"[{marker}] {criterion.name}: {criterion.detail}"
                )
            self._criteria_detail.setText("\n\n".join(lines))
        else:
            self._criteria_detail.setText(
                "No criteria results were returned."
            )

        self.render_orders(result.orders)

    def render_projection(
        self,
        snapshot: OrdersReadModelSnapshot,
    ) -> None:
        """Render an immutable order projection supplied by a presenter."""

        if not isinstance(snapshot, OrdersReadModelSnapshot):
            raise TypeError("snapshot must be an OrdersReadModelSnapshot")

        self._projected_orders = snapshot.orders
        self._rebuild_status_filter()
        self._apply_status_filter(self._status_filter.currentText())

    def _rebuild_status_filter(self) -> None:
        selected = self._status_filter.currentText()

        statuses = sorted(
            (
                {order.status for order in self._projected_orders}
                if self._projected_orders is not None
                else {order.status.value for order in self._orders}
            )
        )

        self._status_filter.blockSignals(True)
        self._status_filter.clear()
        self._status_filter.addItem(self.ALL_STATUSES)
        self._status_filter.addItems(statuses)

        index = self._status_filter.findText(selected)
        self._status_filter.setCurrentIndex(
            index if index >= 0 else 0
        )
        self._status_filter.blockSignals(False)

    def _apply_status_filter(self, selected: str) -> None:
        if self._projected_orders is not None:
            if selected == self.ALL_STATUSES:
                self._visible_projected_orders = self._projected_orders
            else:
                self._visible_projected_orders = tuple(
                    order
                    for order in self._projected_orders
                    if order.status == selected
                )
            self._populate_projection_table()
            return

        if selected == self.ALL_STATUSES:
            self._visible_orders = self._orders
        else:
            self._visible_orders = tuple(
                order
                for order in self._orders
                if order.status.value == selected
            )

        self._populate_table()

    def _populate_projection_table(self) -> None:
        self._orders_table.setSortingEnabled(False)
        self._orders_table.clearContents()
        self._orders_table.clearSpans()

        if not self._visible_projected_orders:
            self._show_empty_state()
            self._clear_selected_order()
            return

        self._orders_table.setRowCount(len(self._visible_projected_orders))
        for row, order in enumerate(self._visible_projected_orders):
            values = (
                order.symbol,
                order.side,
                order.quantity,
                self._table_known(order.filled_quantity),
                self._table_known(order.remaining_quantity),
                self._table_known(order.order_type),
                self._table_price(order.limit_price),
                self._table_price(order.stop_price),
                self._table_price(order.average_fill_price),
                order.status,
                self._compact_timestamp(order.submitted_at),
                self._compact_timestamp(order.updated_at),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, order.order_id)
                if column == 9:
                    item.setForeground(self._status_color(order.status))
                if column in {2, 3, 4, 6, 7, 8}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if column in {10, 11}:
                    item.setToolTip(self._full_timestamp(
                        order.submitted_at if column == 10 else order.updated_at
                    ))
                self._orders_table.setItem(row, column, item)

        count = len(self._visible_projected_orders)
        self._order_count.setText(
            f"{count} order" if count == 1 else f"{count} orders"
        )
        self._orders_table.setSortingEnabled(True)
        self._orders_table.selectRow(0)

    def _populate_table(self) -> None:
        self._orders_table.setSortingEnabled(False)
        self._orders_table.clearContents()
        self._orders_table.clearSpans()

        if not self._visible_orders:
            self._show_empty_state()
            self._clear_selected_order()
            self._orders_table.setSortingEnabled(True)
            return

        self._orders_table.setRowCount(
            len(self._visible_orders)
        )

        for row, order in enumerate(self._visible_orders):
            submitted = self._compact_timestamp(order.submitted_at)

            values = (
                order.symbol,
                order.side.value,
                self._decimal_text(order.requested_quantity),
                self._decimal_text(order.filled_quantity),
                self._decimal_text(order.remaining_quantity),
                order.order_type.value,
                format_price(order.limit_price),
                format_price(order.stop_price),
                format_price(order.average_fill_price),
                order.status.value,
                submitted,
                submitted,
            )

            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    order.broker_order_id,
                )

                if column == 9:
                    item.setForeground(
                        self._status_color(order.status.value)
                    )

                if column in {2, 3, 4, 6, 7, 8}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if column in {10, 11}:
                    item.setToolTip(self._full_timestamp(order.submitted_at))

                self._orders_table.setItem(
                    row,
                    column,
                    item,
                )

        count = len(self._visible_orders)
        self._order_count.setText(
            f"{count} order" if count == 1 else f"{count} orders"
        )

        self._orders_table.setSortingEnabled(True)
        self._orders_table.selectRow(0)

    def _show_empty_state(self) -> None:
        self._orders_table.setSortingEnabled(False)
        self._orders_table.setRowCount(1)
        self._orders_table.clearSpans()

        item = QTableWidgetItem(
            "No active paper orders are available."
        )
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor("#7f8a9a"))

        self._orders_table.setItem(0, 0, item)
        self._orders_table.setSpan(0, 0, 1, 12)
        self._order_count.setText("0 orders")
        self._orders_table.setSortingEnabled(True)

    def _selected_order(self) -> OpenOrderSnapshot | None:
        selected = self._orders_table.selectedItems()

        if not selected or not self._visible_orders:
            return None

        broker_order_id = selected[0].data(
            Qt.ItemDataRole.UserRole
        )

        return next(
            (
                order
                for order in self._visible_orders
                if order.broker_order_id == broker_order_id
            ),
            None,
        )

    def _render_selected_order(self) -> None:
        if self._projected_orders is not None:
            selected = self._orders_table.selectedItems()
            order_id = (
                selected[0].data(Qt.ItemDataRole.UserRole)
                if selected
                else None
            )
            order = next(
                (
                    item
                    for item in self._visible_projected_orders
                    if item.order_id == order_id
                ),
                None,
            )
            self._clear_selected_order()
            if order is not None:
                self._broker_order_id.setText(order.order_id)
                self._submitted_at.setText(
                    self._full_timestamp(order.submitted_at)
                )
                self._updated_at.setText(self._full_timestamp(order.updated_at))
                self._side.setText(order.side)
                self._order_type.setText(self._detail_known(order.order_type))
                self._requested_quantity.setText(order.quantity)
                self._filled_quantity.setText(
                    self._detail_known(order.filled_quantity)
                )
                self._detail_status.setText(order.status)
                self._limit_price.setText(
                    self._detail_price(order.limit_price)
                )
                self._stop_price.setText(
                    self._detail_price(order.stop_price)
                )
                self._average_fill_price.setText(
                    self._detail_price(order.average_fill_price)
                )
                self._remaining_quantity.setText(
                    self._detail_known(order.remaining_quantity)
                )
                self._lifecycle_id.setText(
                    self._detail_known(order.lifecycle_id)
                )
                source = self._detail_known(order.execution_source)
                if order.execution_reason is not None:
                    source = f"{source} / {order.execution_reason}"
                self._execution_source.setText(source)
            return

        order = self._selected_order()

        if order is None:
            self._clear_selected_order()
            return

        self._broker_order_id.setText(order.broker_order_id)
        self._client_order_id.setText(
            order.client_order_id or "--"
        )
        self._account_id.setText(order.account_id)
        self._submitted_at.setText(
            self._full_timestamp(order.submitted_at)
        )
        self._updated_at.setText(self._full_timestamp(order.submitted_at))
        self._side.setText(order.side.value)
        self._order_type.setText(order.order_type.value)
        self._requested_quantity.setText(
            self._decimal_text(order.requested_quantity)
        )
        self._filled_quantity.setText(self._decimal_text(order.filled_quantity))
        self._detail_status.setText(order.status.value)
        self._limit_price.setText(
            format_price(order.limit_price)
        )
        self._stop_price.setText(
            format_price(order.stop_price)
        )
        self._average_fill_price.setText(
            format_price(order.average_fill_price)
        )
        self._remaining_quantity.setText(
            self._decimal_text(order.remaining_quantity)
        )

    def _clear_selected_order(self) -> None:
        for label in (
            self._broker_order_id,
            self._client_order_id,
            self._account_id,
            self._submitted_at,
            self._updated_at,
            self._side,
            self._order_type,
            self._requested_quantity,
            self._filled_quantity,
            self._detail_status,
            self._limit_price,
            self._stop_price,
            self._average_fill_price,
            self._remaining_quantity,
            self._lifecycle_id,
            self._execution_source,
        ):
            label.setText("--")

    @staticmethod
    def _table_known(value: str | None) -> str:
        return "—" if value is None else value

    @staticmethod
    def _detail_known(value: str | None) -> str:
        return "Not available" if value is None else value

    @staticmethod
    def _table_price(value: str | None) -> str:
        return "—" if value is None else format_price(Decimal(value))

    @staticmethod
    def _detail_price(value: str | None) -> str:
        return "Not available" if value is None else format_price(Decimal(value))

    @staticmethod
    def _compact_timestamp(value) -> str:
        return "—" if value is None else value.astimezone().strftime("%m/%d %H:%M:%S")

    @staticmethod
    def _full_timestamp(value) -> str:
        return (
            "Not available"
            if value is None
            else value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        )

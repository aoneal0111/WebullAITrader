from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QTabWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.gui.design.tokens import Colors
from app.gui.models import PositionsSnapshot
from app.gui.models.runtime import RuntimeState
from app.gui.widgets.data_table import StyledDataTable
from app.gui.widgets.orders_panel import MissionControlOrdersPanel


class PositionsPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._runtime_phase: RuntimeState | None = None
        self._runtime_started = False
        self._account_loaded = False
        self._positions_synchronized = False
        self._positions_status: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._management = QFrame()
        self._management.setObjectName("positionManagement")
        management_layout = QVBoxLayout(self._management)
        management_layout.setContentsMargins(8, 6, 8, 6)
        management_layout.setSpacing(5)
        headline = QHBoxLayout()
        self._symbol = QLabel("NO ACTIVE POSITION")
        self._symbol.setObjectName("aiObjective")
        self._position_state = QLabel("Awaiting authoritative position")
        self._position_state.setObjectName("statusBadge")
        headline.addWidget(self._symbol)
        headline.addStretch()
        headline.addWidget(self._position_state)
        management_layout.addLayout(headline)

        facts = QGridLayout()
        facts.setHorizontalSpacing(14)
        facts.setVerticalSpacing(3)
        self._facts: dict[str, QLabel] = {}
        for index, title in enumerate((
            "SIDE / QTY", "AVERAGE ENTRY", "MARK", "UNREALIZED",
            "REALIZED", "STRATEGY / SETUP", "UPDATED", "THESIS",
        )):
            column = index % 4
            row = (index // 4) * 2
            key = QLabel(title)
            key.setObjectName("metricTitle")
            value = QLabel("—")
            value.setObjectName("monitorValue")
            facts.addWidget(key, row, column)
            facts.addWidget(value, row + 1, column)
            facts.setColumnStretch(column, 1)
            self._facts[title] = value
        management_layout.addLayout(facts)

        protection = QHBoxLayout()
        protection_title = QLabel("PROTECTION")
        protection_title.setObjectName("metricTitle")
        self._protection_status = QLabel("NOT EVIDENCED")
        self._protection_status.setObjectName("statusBadge")
        self._protection_detail = QLabel(
            "No reliably correlated protective order in the current projection"
        )
        self._protection_detail.setObjectName("monitorValue")
        protection.addWidget(protection_title)
        protection.addWidget(self._protection_status)
        protection.addWidget(self._protection_detail, 1)
        management_layout.addLayout(protection)
        layout.addWidget(self._management)

        self._table = StyledDataTable(
            (
                "Symbol", "Side", "Size", "Average Entry", "Mark",
                "Unrealized PnL", "PnL %", "Realized PnL", "Updated",
            )
        )
        self._table.set_empty_state(
            "No positions — ACTIVE",
            "Current nonzero exposure will appear here.",
            icon="\u25ce",
        )

        self._closed_table = StyledDataTable(
            (
                "Symbol", "Side", "Size", "Average Entry", "Mark",
                "Unrealized PnL", "PnL %", "Realized PnL", "Updated",
            )
        )
        self._closed_table.set_empty_state(
            "No closed position history",
            "Recently closed PAPER positions will appear here.",
            icon="\u25ce",
        )
        self.recent_orders_panel = MissionControlOrdersPanel()
        self.activity_tabs = QTabWidget()
        self.activity_tabs.setObjectName("positionOrderActivityTabs")
        self.activity_tabs.addTab(self._table, "ACTIVE")
        self.activity_tabs.addTab(self._closed_table, "CLOSED / RECENT")
        self.activity_tabs.addTab(self.recent_orders_panel, "RECENT ORDERS")
        self.recent_orders_panel.active_orders_changed.connect(
            self._show_active_orders
        )
        layout.addWidget(self.activity_tabs, 1)

    def _show_active_orders(self, has_active_orders: bool) -> None:
        if has_active_orders:
            self.activity_tabs.setCurrentWidget(self.recent_orders_panel)

    def render(self, snapshot: PositionsSnapshot) -> None:
        if (
            self._runtime_phase is RuntimeState.STOPPED
            and not self._runtime_started
            and not snapshot.rows
        ):
            self._render_not_started()
            return
        if self._position_snapshot_failed and not snapshot.rows:
            self._render_unavailable(
                "POSITION STATE UNAVAILABLE",
                "BROKER POSITION SNAPSHOT UNAVAILABLE",
                "The authoritative broker position snapshot is unavailable",
                "POSITION STATE UNAVAILABLE",
            )
            return
        if self._runtime_phase in {
            RuntimeState.STARTING, RuntimeState.RUNNING,
        } and not self._positions_synchronized and not snapshot.rows:
            self._render_awaiting_sync()
            return
        if (
            self._runtime_phase is RuntimeState.STOPPED
            and self._runtime_started
            and not self._positions_synchronized
        ):
            self._render_unavailable(
                "POSITION STATE NOT LOADED",
                "RUNTIME STOPPED · POSITION STATE NOT LOADED",
                "No authoritative broker position snapshot was loaded",
                "POSITION STATE NOT LOADED",
            )
            return
        rows = snapshot.rows
        self._render_management(snapshot)
        self._render_rows(self._table, rows)
        self._render_rows(self._closed_table, snapshot.closed_rows)
        self._restore_authoritative_empty_states()

    def set_runtime_phase(
        self,
        phase: RuntimeState,
        *,
        account_loaded: bool = False,
        positions_synchronized: bool | None = None,
        positions_status: str | None = None,
    ) -> None:
        if not isinstance(phase, RuntimeState):
            phase = RuntimeState(str(getattr(phase, "value", phase)))
        if phase in {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.STOPPING}:
            self._runtime_started = True
        self._runtime_phase = phase
        self._account_loaded = bool(account_loaded)
        self._positions_synchronized = bool(
            account_loaded
            if positions_synchronized is None
            else positions_synchronized
        )
        self._positions_status = positions_status
        if phase is RuntimeState.STOPPED and not self._runtime_started:
            self._render_not_started()

    @property
    def _position_snapshot_failed(self) -> bool:
        status = (self._positions_status or "").strip().upper()
        return self._runtime_phase is RuntimeState.FAILED or any(
            token in status for token in ("ERROR", "FAILED", "UNAVAILABLE")
        )

    def _restore_authoritative_empty_states(self) -> None:
        self._table.set_empty_state(
            "NO ACTIVE POSITION",
            "No positions in the authoritative broker snapshot.",
            icon="",
        )
        self._closed_table.set_empty_state(
            "No closed position history",
            "Recently closed PAPER positions will appear here.",
            icon="\u25ce",
        )

    def _render_not_started(self) -> None:
        self._render_unavailable(
            "POSITION STATE NOT LOADED",
            "NOT STARTED",
            "Start Atlas to synchronize broker positions",
            "POSITION STATE NOT LOADED",
        )

    def _render_awaiting_sync(self) -> None:
        self._render_unavailable(
            "POSITION STATE NOT LOADED",
            "AWAITING BROKER SYNCHRONIZATION",
            "Awaiting the authoritative broker position snapshot",
            "AWAITING BROKER SYNCHRONIZATION",
        )

    def _render_unavailable(
        self,
        symbol: str,
        state: str,
        detail: str,
        empty_title: str,
    ) -> None:
        self._symbol.setText(symbol)
        self._position_state.setText(state)
        self._set_status_tone(self._position_state, "neutral")
        for value in self._facts.values():
            value.setText("--")
        self._protection_status.setText(state)
        self._set_status_tone(self._protection_status, "neutral")
        self._protection_detail.setText(detail)
        self._table.setRowCount(0)
        self._closed_table.setRowCount(0)
        self._table.set_empty_state(
            empty_title,
            detail + ".",
            icon="",
        )

    @staticmethod
    def _render_rows(table: StyledDataTable, rows) -> None:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                if column_index >= 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                if column_index == 1:
                    item.setForeground(
                        QBrush(QColor(
                            Colors.SUCCESS if value == "LONG" else
                            Colors.DANGER if value == "SHORT" else
                            Colors.TEXT_MUTED
                        ))
                    )
                if column_index in (5, 6, 7):
                    item.setForeground(
                        QBrush(QColor(_financial_color(value)))
                    )
                table.setItem(row_index, column_index, item)

    def _render_management(self, snapshot: PositionsSnapshot) -> None:
        if not snapshot.management:
            self._symbol.setText("NO ACTIVE POSITION")
            self._position_state.setText("NO ACTIVE POSITION")
            self._set_status_tone(self._position_state, "neutral")
            for value in self._facts.values():
                value.setText("—")
            self._protection_status.setText("NOT APPLICABLE")
            self._set_status_tone(self._protection_status, "neutral")
            self._protection_detail.setText("No active position projected")
            return
        row = snapshot.management[0]
        self._symbol.setText(row.symbol)
        self._position_state.setText(row.management_state.upper())
        self._set_status_tone(
            self._position_state,
            "neutral" if not row.protection_applicable
            else "danger" if row.protection_conflict
            else "warn" if row.protection and row.protection.status == "PARTIALLY_FILLED"
            else "good" if row.protection else "danger",
        )
        self._facts["SIDE / QTY"].setText(f"{row.side}  {row.quantity}")
        self._facts["AVERAGE ENTRY"].setText(row.average_entry)
        self._facts["MARK"].setText(row.mark)
        self._facts["UNREALIZED"].setText(
            f"{row.unrealized_pnl}  {row.unrealized_percent}"
        )
        self._facts["REALIZED"].setText(row.realized_pnl)
        self._facts["STRATEGY / SETUP"].setText(
            f"{row.strategy} / {row.setup}"
        )
        self._facts["UPDATED"].setText(row.updated_at)
        self._facts["THESIS"].setText(row.thesis_state)
        if not row.protection_applicable:
            self._protection_status.setText("NOT APPLICABLE")
            self._set_status_tone(self._protection_status, "neutral")
            self._protection_detail.setText(
                "Protection semantics are not authorized for this exposure"
            )
            return
        if row.protection_conflict:
            self._protection_status.setText("CONFLICTING EVIDENCE")
            self._set_status_tone(self._protection_status, "danger")
            self._protection_detail.setText(
                "Multiple active protective orders correlate to this position"
            )
            return
        if row.protection is None:
            self._protection_status.setText("NOT EVIDENCED")
            self._set_status_tone(self._protection_status, "danger")
            self._protection_detail.setText(
                "No reliably correlated protective order in the current projection"
            )
            return
        protection = row.protection
        self._protection_status.setText(protection.status.replace("_", " "))
        self._set_status_tone(
            self._protection_status,
            "warn" if protection.status == "PARTIALLY_FILLED" else "good",
        )
        self._protection_detail.setText(
            f"{protection.side} {protection.order_type}  ·  "
            f"{protection.remaining_quantity} REMAINING  ·  "
            f"STOP {protection.stop_price}"
        )

    @staticmethod
    def _set_status_tone(label: QLabel, tone: str) -> None:
        label.setProperty("status", tone)
        label.style().unpolish(label)
        label.style().polish(label)


def _financial_color(value: str) -> str:
    if value.startswith("+"):
        return Colors.SUCCESS
    if value.startswith("-"):
        return Colors.DANGER
    return Colors.TEXT_MUTED

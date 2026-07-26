from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.gui.widgets.common import MetricCard, StatusBadge
from app.gui.widgets.panel import SectionPanel
from app.operations_core import ApplicationState, RuntimePhase


class DashboardPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Autonomous Trading Dashboard")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Monitor the runtime, strategy state, risk posture, and execution activity.")
        subtitle.setObjectName("muted")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        self.mode_badge = StatusBadge("PAPER")
        header.addLayout(heading)
        header.addStretch()
        header.addWidget(self.mode_badge)
        root.addLayout(header)

        metrics = QGridLayout()
        metrics.setSpacing(12)
        self.runtime = MetricCard("Runtime", "Stopped", "Safe and idle")
        self.broker = MetricCard("Broker", "Disconnected", "No account mutations")
        self.market = MetricCard("Market Feed", "Idle", "Awaiting runtime")
        self.model = MetricCard("Active Model", "Not loaded", "No strategy active")
        self.cycles = MetricCard("Runtime Cycles", "0", "Completed cycles")
        self.risk = MetricCard("Safety State", "Protected", "Emergency stop enabled")
        for i, card in enumerate((self.runtime, self.broker, self.market, self.model, self.cycles, self.risk)):
            metrics.addWidget(card, i // 3, i % 3)
        root.addLayout(metrics)

        body = QGridLayout()
        body.setSpacing(12)
        self.activity = QLabel("No operations events recorded.")
        self.activity.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.activity.setWordWrap(True)
        body.addWidget(SectionPanel("Decision & Activity Feed", self.activity), 0, 0, 2, 2)

        positions = QTableWidget(3, 4)
        positions.setHorizontalHeaderLabels(("Symbol", "Qty", "Avg Price", "P/L"))
        fixture = (("--", "--", "--", "--"),) * 3
        for r, row in enumerate(fixture):
            for c, value in enumerate(row):
                positions.setItem(r, c, QTableWidgetItem(value))
        positions.horizontalHeader().setStretchLastSection(True)
        positions.verticalHeader().setVisible(False)
        positions.setEnabled(False)
        body.addWidget(SectionPanel("Open Positions", positions), 0, 2)

        orders = QTableWidget(3, 3)
        orders.setHorizontalHeaderLabels(("Order", "Status", "Updated"))
        for r in range(3):
            for c in range(3):
                orders.setItem(r, c, QTableWidgetItem("--"))
        orders.horizontalHeader().setStretchLastSection(True)
        orders.verticalHeader().setVisible(False)
        orders.setEnabled(False)
        body.addWidget(SectionPanel("Active Orders", orders), 1, 2)
        body.setColumnStretch(0, 2)
        body.setColumnStretch(1, 2)
        body.setColumnStretch(2, 3)
        root.addLayout(body, 1)

    def render(self, state: ApplicationState) -> None:
        runtime = state.runtime
        self.runtime.set_value(runtime.phase.value.title(), "Runtime lifecycle state")
        self.broker.set_value(runtime.broker_status, "Broker gateway status")
        self.market.set_value(runtime.market_feed_status, "Market data connection")
        self.model.set_value(runtime.active_model, runtime.inference_status)
        self.cycles.set_value(str(runtime.cycles_completed), "Completed runtime cycles")
        self.risk.set_value("Protected", "Emergency stop enabled")
        level = "good" if runtime.phase is RuntimePhase.RUNNING else "warn"
        self.mode_badge.set_status(runtime.environment, level)
        if state.timeline:
            self.activity.setText("\n\n".join(
                f"{entry.occurred_at.astimezone():%H:%M:%S}   {entry.message}"
                for entry in state.timeline[-10:][::-1]
            ))
        else:
            self.activity.setText("No operations events recorded.")

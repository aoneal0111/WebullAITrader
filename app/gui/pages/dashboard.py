from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import DashboardSnapshot
from app.gui.widgets.common import StatusBadge
from app.gui.widgets.panel import SectionPanel
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.runtime_ribbon import RuntimeRibbon


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

        subtitle = QLabel(
            "Monitor the runtime, strategy state, risk posture, and execution activity."
        )
        subtitle.setObjectName("muted")

        heading.addWidget(title)
        heading.addWidget(subtitle)

        self.mode_badge = StatusBadge("PAPER")

        header.addLayout(heading)
        header.addStretch()
        header.addWidget(self.mode_badge)

        root.addLayout(header)

        self.runtime_ribbon = RuntimeRibbon()
        root.addWidget(self.runtime_ribbon)

        body = QGridLayout()
        body.setSpacing(12)

        self.activity_panel = ActivityPanel()

        body.addWidget(
            SectionPanel("Decision & Activity Feed", self.activity_panel),
            0,
            0,
            2,
            2,
        )

        positions = QTableWidget(3, 4)
        positions.setHorizontalHeaderLabels(("Symbol", "Qty", "Avg Price", "P/L"))

        for r in range(3):
            for c in range(4):
                positions.setItem(r, c, QTableWidgetItem("--"))

        positions.horizontalHeader().setStretchLastSection(True)
        positions.verticalHeader().setVisible(False)
        positions.setEnabled(False)

        body.addWidget(
            SectionPanel("Open Positions", positions),
            0,
            2,
        )

        orders = QTableWidget(3, 3)
        orders.setHorizontalHeaderLabels(("Order", "Status", "Updated"))

        for r in range(3):
            for c in range(3):
                orders.setItem(r, c, QTableWidgetItem("--"))

        orders.horizontalHeader().setStretchLastSection(True)
        orders.verticalHeader().setVisible(False)
        orders.setEnabled(False)

        body.addWidget(
            SectionPanel("Active Orders", orders),
            1,
            2,
        )

        body.setColumnStretch(0, 2)
        body.setColumnStretch(1, 2)
        body.setColumnStretch(2, 3)

        root.addLayout(body, 1)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_ribbon.render(snapshot)

        level = (
            "good"
            if snapshot.runtime_state.value == "RUNNING"
            else "warn"
        )

        self.mode_badge.set_status(
            snapshot.environment,
            level,
        )

        self.activity_panel.render(snapshot.activity)

from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import DashboardSnapshot
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.common import StatusBadge
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.panel import SectionPanel
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.runtime_ribbon import RuntimeRibbon
from app.gui.widgets.portfolio_panel import PortfolioPanel
from app.gui.widgets.health_panel import HealthPanel


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
        self.positions_panel = PositionsPanel()
        self.orders_panel = OrdersPanel()
        self.portfolio_panel = PortfolioPanel()
        self.health_panel = HealthPanel()

        body.addWidget(
            SectionPanel(
                "Decision & Activity Feed",
                self.activity_panel,
            ),
            0,
            0,
            2,
            2,
        )

        body.addWidget(
            SectionPanel(
                "Open Positions",
                self.positions_panel,
            ),
            0,
            2,
        )

        body.addWidget(
            SectionPanel(
                "Active Orders",
                self.orders_panel,
            ),
            1,
            2,
        )
        body.addWidget(
            SectionPanel(
                "Portfolio Summary",
                self.portfolio_panel,
            ),
            2,
            0,
            1,
            3,
        )
        body.addWidget(
            SectionPanel(
                "Infrastructure Health",
                self.health_panel,
            ),
            3,
            0,
            1,
            3,
        )

        body.setColumnStretch(0, 2)
        body.setColumnStretch(1, 2)
        body.setColumnStretch(2, 3)

        root.addLayout(body, 1)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_ribbon.render(snapshot.runtime)

        level = (
            "good"
            if snapshot.runtime.state.value == "RUNNING"
            else "warn"
        )

        self.mode_badge.set_status(
            snapshot.runtime.environment,
            level,
        )

        self.activity_panel.render(snapshot.activity)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)

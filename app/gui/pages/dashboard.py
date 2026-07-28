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
from app.gui.widgets.decision_center import DecisionCenter
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.panel import SectionPanel
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.portfolio_metrics import PortfolioMetrics
from app.gui.widgets.runtime_ribbon import RuntimeRibbon
from app.gui.widgets.runtime_health_panel import RuntimeHealthPanel


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

        self.portfolio_metrics = PortfolioMetrics()
        root.addWidget(self.portfolio_metrics)

        self.runtime_health_panel = RuntimeHealthPanel()
        root.addWidget(
            SectionPanel(
                "Runtime Health Center - Read Only",
                self.runtime_health_panel,
            )
        )

        body = QGridLayout()
        body.setSpacing(12)

        self.activity_panel = ActivityPanel()
        self.decision_center = DecisionCenter()
        self.positions_panel = PositionsPanel()
        self.orders_panel = OrdersPanel()

        body.addWidget(
            SectionPanel(
                "AI Decision Center - Read Only",
                self.decision_center,
            ),
            0,
            0,
            1,
            2,
        )

        body.addWidget(
            SectionPanel(
                "Operations Activity",
                self.activity_panel,
            ),
            1,
            0,
            1,
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

        body.setColumnStretch(0, 2)
        body.setColumnStretch(1, 2)
        body.setColumnStretch(2, 3)

        root.addLayout(body, 1)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_ribbon.render(snapshot.runtime)
        self.portfolio_metrics.render(snapshot.portfolio)
        self.runtime_health_panel.render(snapshot.runtime_health)

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
        self.decision_center.render(snapshot.decisions)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)

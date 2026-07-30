from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import DashboardSnapshot
from app.gui.widgets.infrastructure_strip import InfrastructureStrip
from app.gui.widgets.market_workspace import MarketWorkspace
from app.gui.widgets.operator_workspace import OperatorWorkspace
from app.gui.widgets.portfolio_summary_strip import PortfolioSummaryStrip
from app.gui.widgets.runtime_control_header import RuntimeControlHeader


class DashboardPage(QWidget):
    """Responsive Atlas terminal dashboard composed from focused views."""

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        content = QWidget()
        content.setObjectName("contentArea")
        content.setMinimumWidth(900)
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 4, 8, 16)
        root.setSpacing(12)

        heading = QHBoxLayout()
        titles = QVBoxLayout()
        eyebrow = QLabel("OPERATIONS / OVERVIEW")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Atlas Trading Dashboard")
        title.setObjectName("pageTitle")
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        heading.addLayout(titles)
        heading.addStretch()
        root.addLayout(heading)

        self.runtime_header = RuntimeControlHeader()
        self.infrastructure = InfrastructureStrip()
        self.portfolio_summary = PortfolioSummaryStrip()
        self.market_workspace = MarketWorkspace()
        self.operator_workspace = OperatorWorkspace()

        root.addWidget(self.runtime_header)
        root.addWidget(self.infrastructure)
        root.addWidget(self.portfolio_summary)
        root.addWidget(self.market_workspace, 3)
        root.addWidget(self.operator_workspace, 2)

        # Compatibility aliases retained for existing focused presenters/tests.
        self.positions_panel = self.operator_workspace.positions
        self.orders_panel = self.operator_workspace.orders
        self.activity_panel = self.operator_workspace.timeline
        self.decisions_panel = self.operator_workspace.decisions
        self.portfolio_panel = self.portfolio_summary
        self.health_panel = self.infrastructure
        self.operator_health_panel = self.operator_workspace.health
        self.replay_status_panel = self.operator_workspace.lifecycle

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_header.render(snapshot.runtime)
        self.activity_panel.render(snapshot.activity)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)

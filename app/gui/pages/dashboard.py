from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import DashboardSnapshot
from app.gui.widgets.market_workspace import MarketWorkspace
from app.gui.widgets.infrastructure_strip import InfrastructureStrip
from app.gui.widgets.mission_status_panel import MissionStatusPanel
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
        content.setMinimumWidth(760)
        root = QVBoxLayout(content)
        root.setContentsMargins(8, 6, 10, 10)
        root.setSpacing(10)

        heading = QHBoxLayout()
        titles = QVBoxLayout()
        eyebrow = QLabel("OPERATIONS / OVERVIEW")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Atlas Mission Control")
        title.setObjectName("pageTitle")
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        heading.addLayout(titles)
        heading.addStretch()
        root.addLayout(heading)

        self.runtime_header = RuntimeControlHeader()
        self.mission_status = MissionStatusPanel()
        self.infrastructure = InfrastructureStrip()
        self.portfolio_summary = PortfolioSummaryStrip()
        self.market_workspace = MarketWorkspace()
        self.operator_workspace = OperatorWorkspace()

        self.summary_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.summary_splitter.setHandleWidth(2)
        infrastructure_column = QWidget()
        infrastructure_layout = QVBoxLayout(infrastructure_column)
        infrastructure_layout.setContentsMargins(0, 0, 0, 0)
        infrastructure_layout.setSpacing(6)
        infrastructure_layout.addWidget(self.infrastructure)
        mission_heading = QLabel("MISSION STATUS")
        mission_heading.setObjectName("sectionEyebrow")
        infrastructure_layout.addWidget(mission_heading)
        infrastructure_layout.addWidget(self.mission_status)
        infrastructure_group = _section_group(
            "Infrastructure", infrastructure_column
        )
        portfolio_group = _section_group(
            "Portfolio Overview", self.portfolio_summary
        )
        # Let the portfolio grid choose its height from the available width.
        # Fixed maximum heights clipped the second row at medium/compact sizes.
        self.summary_splitter.addWidget(infrastructure_group)
        self.summary_splitter.addWidget(portfolio_group)
        self.summary_splitter.setCollapsible(0, False)
        self.summary_splitter.setCollapsible(1, False)
        self.summary_splitter.setStretchFactor(0, 3)
        self.summary_splitter.setStretchFactor(1, 2)
        self.summary_splitter.setSizes((780, 520))
        root.addWidget(self.runtime_header)
        root.addWidget(self.summary_splitter)
        root.addWidget(self.market_workspace, 1)

        # OperatorWorkspace is intentionally not placed on Dashboard.
        # MainWindow reparents it into the dedicated Operations navigation page.

        # Compatibility aliases retained for existing focused presenters/tests.
        self.positions_panel = self.operator_workspace.positions
        self.orders_panel = self.operator_workspace.orders
        self.activity_panel = self.operator_workspace.timeline
        self.decisions_panel = self.operator_workspace.decisions
        self.portfolio_panel = self.portfolio_summary
        self.operator_health_panel = self.operator_workspace.health
        self.health_panel = self.operator_health_panel
        self.replay_status_panel = self.operator_workspace.lifecycle
        self.paper_validation_panel = self.operator_workspace.paper_validation

        scroll.setWidget(content)
        self._scroll = scroll
        outer.addWidget(scroll)

    def resizeEvent(self, event) -> None:
        width = event.size().width()
        # Wide screens keep the summary strip across the top. Medium/compact
        # screens stack it so Mission Status and portfolio values stay legible.
        orientation = (
            Qt.Orientation.Horizontal
            if width >= 1240
            else Qt.Orientation.Vertical
        )
        if self.summary_splitter.orientation() != orientation:
            self.summary_splitter.setOrientation(orientation)
        if orientation == Qt.Orientation.Horizontal:
            self.summary_splitter.setSizes((680, 600))
        else:
            self.summary_splitter.setSizes((210, 230))
        super().resizeEvent(event)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_header.render(snapshot.runtime)
        self.activity_panel.render(snapshot.activity)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)
        self.paper_validation_panel.render(snapshot.paper_validation)
        self.market_workspace.render_activity(snapshot.atlas_activity)
        self.mission_status.render(snapshot.mission_status)
        self.market_workspace.render_ai_thinking(snapshot.ai_thinking)


def _section_group(title: str, content: QWidget) -> QWidget:
    group = QWidget()
    layout = QVBoxLayout(group)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    heading = QLabel(title.upper())
    heading.setObjectName("sectionEyebrow")
    layout.addWidget(heading)
    layout.addWidget(content)
    return group

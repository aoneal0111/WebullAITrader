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
        content.setMinimumWidth(860)
        root = QVBoxLayout(content)
        root.setContentsMargins(4, 2, 6, 2)
        root.setSpacing(7)

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
        self.summary_splitter = QSplitter(Qt.Orientation.Vertical)
        self.summary_splitter.setHandleWidth(2)
        infrastructure_group = _section_group(
            "Infrastructure", self.infrastructure
        )
        portfolio_group = _section_group(
            "Portfolio Summary", self.portfolio_summary
        )
        self.summary_splitter.addWidget(infrastructure_group)
        self.summary_splitter.addWidget(portfolio_group)
        self.summary_splitter.setCollapsible(0, False)
        self.summary_splitter.setCollapsible(1, False)
        self.summary_splitter.setStretchFactor(0, 1)
        self.summary_splitter.setStretchFactor(1, 2)
        root.addWidget(self.summary_splitter)
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
        self.paper_validation_panel = self.operator_workspace.paper_validation

        scroll.setWidget(content)
        self._scroll = scroll
        outer.addWidget(scroll)

    def resizeEvent(self, event) -> None:
        self.summary_splitter.setOrientation(
            Qt.Orientation.Horizontal
            if event.size().width() >= 1380
            else Qt.Orientation.Vertical
        )
        super().resizeEvent(event)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_header.render(snapshot.runtime)
        self.activity_panel.render(snapshot.activity)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)
        self.paper_validation_panel.render(snapshot.paper_validation)


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

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from app.gui.models import DashboardSnapshot
from app.gui.widgets.market_workspace import MarketWorkspace
from app.gui.widgets.operator_workspace import OperatorWorkspace
from app.gui.widgets.portfolio_summary_strip import PortfolioSummaryStrip
from app.gui.widgets.runtime_control_header import RuntimeControlHeader


class DashboardPage(QWidget):
    """Responsive Atlas terminal dashboard composed from focused views."""

    def __init__(self) -> None:
        super().__init__()
        self._external_viewport_width: int | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        content.setObjectName("contentArea")
        content.setMinimumWidth(760)
        root = QVBoxLayout(content)
        root.setContentsMargins(6, 6, 8, 8)
        root.setSpacing(7)

        self.runtime_header = RuntimeControlHeader()
        self.portfolio_summary = PortfolioSummaryStrip()
        self.portfolio_summary.setParent(self)
        self.portfolio_summary.hide()
        self.market_workspace = MarketWorkspace()
        self.operator_workspace = OperatorWorkspace()
        self.mission_status = self.market_workspace.mission_status
        self.infrastructure = self.market_workspace.infrastructure
        self.main_splitter = self.market_workspace.splitter
        root.addWidget(self.runtime_header)
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
        width = self._external_viewport_width or event.size().width()
        self.market_workspace.set_responsive_width(width)
        super().resizeEvent(event)

    def set_viewport_width(self, width: int, *, force: bool = False) -> None:
        self._external_viewport_width = width
        self.market_workspace.set_responsive_width(
            width, force=force, external=True
        )

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_header.render(snapshot.runtime)
        self.activity_panel.render(snapshot.activity)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)
        self.paper_validation_panel.render(snapshot.paper_validation)
        self.market_workspace.render_activity(snapshot.atlas_activity)
        self.market_workspace.render_mission(snapshot.mission_status)
        self.market_workspace.render_ai_thinking(snapshot.ai_thinking)

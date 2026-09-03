from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.gui.models import DashboardSnapshot
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.market_workspace import MarketWorkspace
from app.gui.widgets.operator_workspace import OperatorWorkspace
from app.gui.widgets.runtime_control_header import RuntimeControlHeader
from app.gui.widgets.workstation_panels import WorkstationFooter


class DashboardPage(QWidget):
    """Single-screen, non-scrolling Atlas supervision workstation."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("appRoot")
        self._external_viewport_width: int | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 8, 6)
        root.setSpacing(6)
        self.market_workspace = MarketWorkspace()
        self.runtime_header = RuntimeControlHeader(
            self.market_workspace.runtime_controls
        )
        self.workstation_header = self.runtime_header
        # Presenter compatibility aliases point at the visible workstation controls.
        self.runtime_header.resume_button = self.market_workspace.runtime_controls.start_button
        self.runtime_header.stop_button = self.market_workspace.runtime_controls.stop_button
        self.runtime_header.inspector_button = self.market_workspace.runtime_controls.inspector_button
        self.workstation_footer = WorkstationFooter()
        self.market_workspace.runtime_controls.set_footer_view(
            self.workstation_footer
        )
        self.operator_workspace = OperatorWorkspace()
        self.mission_status = self.market_workspace.mission_status
        self.infrastructure = self.market_workspace.infrastructure
        self.main_splitter = self.market_workspace.splitter
        root.addWidget(self.runtime_header)
        root.addWidget(self.market_workspace, 1)
        root.addWidget(self.workstation_footer)

        self.portfolio_summary = self.market_workspace.portfolio_summary
        self.live_activity = self.market_workspace.activity_panel
        self.live_activity_section = self.market_workspace.activity_section
        self.activity_panel = self.live_activity
        self.positions_panel = self.market_workspace.positions_panel
        self.orders_panel = self.market_workspace.orders_panel
        self.operations_activity_panel = self.operator_workspace.timeline
        self.decisions_panel = self.operator_workspace.decisions
        self.portfolio_panel = self.portfolio_summary
        self.operator_health_panel = self.operator_workspace.health
        self.health_panel = self.operator_health_panel
        self.replay_status_panel = self.operator_workspace.lifecycle
        self.paper_validation_panel = self.operator_workspace.paper_validation

    def resizeEvent(self, event) -> None:
        width = self._external_viewport_width or event.size().width()
        self.market_workspace.set_responsive_width(width)
        super().resizeEvent(event)

    def set_viewport_width(self, width: int, *, force: bool = False) -> None:
        self._external_viewport_width = width
        self.market_workspace.set_responsive_width(width, force=force, external=True)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_header.render(snapshot.runtime)
        self.workstation_footer.set_value(
            "Mode", snapshot.runtime.environment.upper() or "--"
        )
        self.market_workspace.render_activity(snapshot.atlas_activity)
        self.market_workspace.render_mission(snapshot.mission_status)
        self.market_workspace.render_ai_thinking(snapshot.ai_thinking)
        self.market_workspace.render_atlas_reasoning(snapshot.atlas_reasoning)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)
        self.operator_workspace.positions.render(snapshot.positions)
        self.operator_workspace.orders.render(snapshot.orders)
        self.paper_validation_panel.render(snapshot.paper_validation)


__all__ = ["DashboardPage"]

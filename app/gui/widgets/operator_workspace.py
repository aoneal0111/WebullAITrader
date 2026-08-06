from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget

from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.decisions_panel import DecisionsPanel
from app.gui.widgets.health_panel import HealthPanel
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.paper_validation_panel import PaperValidationPanel
from app.gui.widgets.replay_status_panel import ReplayStatusPanel
from app.gui.design.tokens import Dimensions


class OperatorWorkspace(QWidget):
    """Projection-backed tabbed workspace used by the dashboard shell."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(Dimensions.OPERATOR_MIN_HEIGHT)
        self.tabs.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Ignored,
        )
        self.positions = PositionsPanel()
        self.orders = OrdersPanel()
        self.decisions = DecisionsPanel()
        self.timeline = ActivityPanel()
        self.lifecycle = ReplayStatusPanel()
        self.health = HealthPanel()
        self.paper_validation = PaperValidationPanel()
        health_workspace = QWidget()
        health_workspace.setObjectName("healthWorkspace")
        health_layout = QHBoxLayout(health_workspace)
        health_layout.setContentsMargins(0, 0, 0, 0)
        self.health_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.health_splitter.addWidget(self.health)
        self.health_splitter.addWidget(self.paper_validation)
        self.health_splitter.setStretchFactor(0, 3)
        self.health_splitter.setStretchFactor(1, 2)
        health_layout.addWidget(self.health_splitter)
        self.tabs.addTab(self.positions, "Positions")
        self.tabs.addTab(self.orders, "Orders")
        self.tabs.addTab(self.decisions, "Decisions")
        self.tabs.addTab(self.timeline, "Timeline")
        self.tabs.addTab(self.lifecycle, "Lifecycle")
        self.tabs.addTab(health_workspace, "System Health")
        layout.addWidget(self.tabs)

    def resizeEvent(self, event) -> None:
        self.health_splitter.setOrientation(
            Qt.Orientation.Vertical
            if event.size().width() < 850
            else Qt.Orientation.Horizontal
        )
        super().resizeEvent(event)


__all__ = ["OperatorWorkspace"]

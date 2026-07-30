from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QStatusBar, QVBoxLayout, QWidget

from app.gui.design.theme import application_stylesheet
from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.pages.placeholder import PlaceholderPage
from app.gui.presenters import (
    DashboardPresenter,
    DecisionsPresenter,
    PortfolioPresenter,
    OrdersPresenter,
    PositionsPresenter,
    PresentationCoordinator,
    RuntimeControlsPresenter,
    RuntimeErrorPresenter,
    RuntimeStatusPresenter,
    TimelinePresenter,
)
from app.gui.shell.sidebar import Sidebar
from app.gui.state_bridge import QtStateBridge
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.decisions_panel import DecisionsPanel
from app.operations_core import ApplicationState, ApplicationStateStore, OperationsBus
from app.services import OrderCommandFactory, RuntimeService, TradingService


class MainWindow(QMainWindow):
    def __init__(
        self,
        bus: OperationsBus,
        state_store: ApplicationStateStore,
        runtime_service: RuntimeService,
        trading_service: TradingService | None = None,
        order_command_factory: OrderCommandFactory | None = None,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._state_store = state_store
        self._runtime_service = runtime_service
        self._trading_service = trading_service
        self._order_command_factory = order_command_factory
        self._state_bridge = QtStateBridge(state_store, self)
        self._state_bridge.state_changed.connect(self._render_state)
        self.setWindowTitle("Atlas — WebullAITrader")
        self.setMinimumSize(1180, 760)
        self.resize(1440, 900)
        self._build()
        self._presentation = PresentationCoordinator(
            (
                DashboardPresenter(self.dashboard),
                OrdersPresenter(self.orders),
                PositionsPresenter(self.positions),
                TimelinePresenter(self.activity),
                DecisionsPresenter(self.decisions),
                PortfolioPresenter(self.dashboard.portfolio_panel),
                RuntimeControlsPresenter(self.start_button, self.stop_button),
                RuntimeStatusPresenter(self.status_label),
                RuntimeErrorPresenter(self),
            )
        )
        self.setStyleSheet(application_stylesheet())
        self._render_state(state_store.snapshot())

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.sidebar = Sidebar()
        outer.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 18, 24, 18)
        content_layout.setSpacing(16)
        controls = QHBoxLayout()
        system = QLabel("SYSTEM CONTROL")
        system.setObjectName("sectionTitle")
        self.start_button = QPushButton("Start Paper Runtime")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._runtime_service.start)
        self.stop_button = QPushButton("Stop Runtime")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.clicked.connect(lambda checked=False: self._runtime_service.stop())
        self.emergency_button = QPushButton("Emergency Stop")
        self.emergency_button.setObjectName("dangerButton")
        self.emergency_button.clicked.connect(self._emergency_stop)
        controls.addWidget(system)
        controls.addStretch()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.emergency_button)
        content_layout.addLayout(controls)

        self.pages = QStackedWidget()
        self.dashboard = DashboardPage()
        self.pages.addWidget(self.dashboard)
        self.positions = PositionsPanel()
        self.pages.addWidget(self.positions)

        self.orders = OrdersPage(
            trading_service=self._trading_service,
            order_command_factory=self._order_command_factory,
        )
        self.pages.addWidget(self.orders)

        self.pages.addWidget(
            PlaceholderPage(
                "Strategies",
                "Strategy selection, promotion state, and autonomous runtime controls will live here.",
            )
        )

        self.pages.addWidget(
            PlaceholderPage(
                "Risk",
                "Risk limits, safety gates, and live-trading permissions will be surfaced here.",
            )
        )

        self.activity = ActivityPanel()
        self.pages.addWidget(self.activity)
        self.decisions = DecisionsPanel()
        self.pages.addWidget(self.decisions)
        content_layout.addWidget(self.pages, 1)
        outer.addWidget(content, 1)
        self.sidebar.page_requested.connect(self.pages.setCurrentIndex)
        self.setCentralWidget(root)

        status = QStatusBar()
        self.status_label = QLabel()
        status.addWidget(self.status_label, 1)
        safety = QLabel("PAPER MODE · NO LIVE BROKER MUTATIONS")
        safety.setObjectName("muted")
        status.addPermanentWidget(safety)
        self.setStatusBar(status)

    def _emergency_stop(self) -> None:
        self._runtime_service.stop()
        self.statusBar().showMessage("Emergency stop requested. Runtime shutdown in progress.", 5000)

    def _render_state(self, state: ApplicationState) -> None:
        self._presentation.render(state)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._runtime_service.close(timeout_seconds=5.0):
            QMessageBox.warning(self, "Runtime Still Stopping", "Stop the runtime and wait for shutdown before closing.")
            event.ignore()
            return
        self._state_bridge.close()
        event.accept()


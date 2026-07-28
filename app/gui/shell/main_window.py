from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QStatusBar, QVBoxLayout, QWidget

from app.gui.design.theme import application_stylesheet
from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.pages.placeholder import PlaceholderPage
from app.gui.projections.dashboard_projection import project_dashboard
from app.gui.shell.sidebar import Sidebar
from app.gui.state_bridge import QtStateBridge
from app.operations_core import ApplicationState, ApplicationStateStore, OperationsBus, RuntimePhase
from app.services import RuntimeService
from app.read_models.decisions import DecisionProjector
from app.read_models.runtime_health import RuntimeHealthProjector
from app.read_models.timeline import TimelineProjector
from app.read_models.trade_lifecycle import TradeLifecycleProjector


class MainWindow(QMainWindow):
    def __init__(
        self,
        bus: OperationsBus,
        state_store: ApplicationStateStore,
        runtime_service: RuntimeService,
        decision_projector: DecisionProjector,
        runtime_health_projector: RuntimeHealthProjector,
        timeline_projector: TimelineProjector,
        trade_lifecycle_projector: TradeLifecycleProjector,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._state_store = state_store
        self._runtime_service = runtime_service
        self._decision_projector = decision_projector
        self._runtime_health_projector = runtime_health_projector
        self._timeline_projector = timeline_projector
        self._trade_lifecycle_projector = trade_lifecycle_projector
        self._last_error = ""
        self._state_bridge = QtStateBridge(state_store, self)
        self._state_bridge.state_changed.connect(self._render_state)
        self.setWindowTitle("Atlas — WebullAITrader")
        self.setMinimumSize(1180, 760)
        self.resize(1440, 900)
        self._build()
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
        self.pages.addWidget(
            PlaceholderPage(
                "Positions",
                "Portfolio views will bind to the existing position and account-state services.",
            )
        )

        self.orders = OrdersPage()
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

        self.pages.addWidget(
            PlaceholderPage(
                "Activity",
                "Auditable system events, decisions, warnings, and execution records will appear here.",
            )
        )
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
        dashboard_snapshot = project_dashboard(
            state,
            self._decision_projector.snapshot(),
            self._runtime_health_projector.snapshot(),
            self._timeline_projector.snapshot(),
            self._trade_lifecycle_projector.snapshot(),
        )
        self.dashboard.render(dashboard_snapshot)
        phase = state.runtime.phase
        active = phase in {RuntimePhase.STARTING, RuntimePhase.RUNNING, RuntimePhase.STOPPING}
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(phase in {RuntimePhase.STARTING, RuntimePhase.RUNNING})
        self.status_label.setText(
            f"{state.runtime.environment}  |  Runtime {phase.value}  |  "
            f"Feed {state.runtime.market_feed_status}  |  Cycles {state.runtime.cycles_completed}"
        )
        if phase is RuntimePhase.FAILED and state.runtime.last_error and state.runtime.last_error != self._last_error:
            self._last_error = state.runtime.last_error
            QMessageBox.critical(self, "Runtime Error", state.runtime.last_error)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._runtime_service.close(timeout_seconds=5.0):
            QMessageBox.warning(self, "Runtime Still Stopping", "Stop the runtime and wait for shutdown before closing.")
            event.ignore()
            return
        self._state_bridge.close()
        event.accept()


from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.gui.design.theme import application_stylesheet
from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.pages.placeholder import PlaceholderPage
from app.gui.pages.replay import ReplayPage
from app.gui.presenters import (
    DashboardPresenter,
    DecisionsPresenter,
    HealthPresenter,
    OrdersPresenter,
    PortfolioPresenter,
    PositionsPresenter,
    PresentationCoordinator,
    ReplayPresenter,
    RuntimeControlsPresenter,
    RuntimeErrorPresenter,
    RuntimeStatusPresenter,
    TimelinePresenter,
    WatchlistPresenter,
)
from app.gui.replay_state_bridge import QtReplayStateBridge
from app.gui.shell.sidebar import Sidebar
from app.gui.state_bridge import QtStateBridge
from app.gui.view_adapters import RenderAdapter
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.decisions_panel import DecisionsPanel
from app.gui.widgets.global_status_bar import GlobalStatusBar
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.watchlist_panel import WatchlistPanel
from app.operations_core import (
    ApplicationState,
    ApplicationStateStore,
    OperationsBus,
)
from app.replay_workspace import ReplayWorkspace
from app.services import OrderCommandFactory, RuntimeService, TradingService


class MainWindow(QMainWindow):
    def __init__(
        self,
        bus: OperationsBus,
        state_store: ApplicationStateStore,
        runtime_service: RuntimeService,
        trading_service: TradingService | None = None,
        order_command_factory: OrderCommandFactory | None = None,
        replay_workspace: ReplayWorkspace | None = None,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._state_store = state_store
        self._runtime_service = runtime_service
        self._trading_service = trading_service
        self._order_command_factory = order_command_factory
        self._replay_workspace = replay_workspace
        self._replay_state_bridge: QtReplayStateBridge | None = None
        self._state_bridge = QtStateBridge(state_store, self)
        self._state_bridge.state_changed.connect(self._render_state)
        self.setWindowTitle("Atlas \u2014 WebullAITrader")
        self.setMinimumSize(1180, 760)
        self.resize(1440, 900)
        self._build()
        self._build_presentation()
        self.setStyleSheet(application_stylesheet())
        self._render_state(state_store.snapshot())
        if replay_workspace is not None:
            self._wire_replay_workspace(replay_workspace)
            self._render_state(replay_workspace.state)
        self.sidebar.buttons["Dashboard"].setFocus()

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.sidebar = Sidebar()
        outer.addWidget(self.sidebar)

        content = QWidget()
        content.setObjectName("contentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 9, 12, 5)
        content_layout.setSpacing(8)
        self.pages = QStackedWidget()
        self.dashboard = DashboardPage()
        self.pages.addWidget(self.dashboard)  # 0
        self.positions = PositionsPanel()
        self.pages.addWidget(self.positions)  # 1
        self.orders = OrdersPage(
            trading_service=self._trading_service,
            order_command_factory=self._order_command_factory,
        )
        self.pages.addWidget(self.orders)  # 2
        self.pages.addWidget(
            PlaceholderPage(
                "Strategies",
                "Strategy selection and promotion experiments.",
            )
        )  # 3
        self.pages.addWidget(
            PlaceholderPage(
                "Risk & Settings",
                "Risk limits, safety gates, and operator preferences.",
            )
        )  # 4
        self.activity = ActivityPanel()
        self.pages.addWidget(self.activity)  # 5
        self.decisions = DecisionsPanel()
        self.pages.addWidget(self.decisions)  # 6
        self.watchlist = WatchlistPanel()
        self.pages.addWidget(self.watchlist)  # 7
        self.replay = ReplayPage()
        self.pages.addWidget(self.replay)  # 8
        content_layout.addWidget(self.pages, 1)
        outer.addWidget(content, 1)
        self.sidebar.page_requested.connect(self.pages.setCurrentIndex)
        self.setCentralWidget(root)

        header = self.dashboard.runtime_header
        self.start_button = header.resume_button
        self.stop_button = header.stop_button
        self.pause_button = header.pause_button
        self.flatten_button = header.flatten_button
        self.emergency_button = self.flatten_button
        self.start_button.clicked.connect(self._runtime_service.start)
        self.stop_button.clicked.connect(
            lambda checked=False: self._runtime_service.stop()
        )
        self.pause_button.clicked.connect(self._toggle_replay)

        status = QStatusBar()
        self.global_status = GlobalStatusBar(version=_application_version())
        status.addWidget(self.global_status, 1)
        self.status_label = QLabel()
        self.status_label.setVisible(False)
        status.addWidget(self.status_label)
        self.setStatusBar(status)

    def _build_presentation(self) -> None:
        self._timeline_presenter = TimelinePresenter(
            self.activity,
            self.dashboard.activity_panel,
        )
        self._decisions_presenter = DecisionsPresenter(
            self.decisions,
            self.dashboard.decisions_panel,
        )
        self._watchlist_presenter = WatchlistPresenter(
            self.watchlist,
            self.dashboard.market_workspace,
        )
        self._replay_presenter = ReplayPresenter(
            self.replay,
            self.dashboard.replay_status_panel,
            RenderAdapter(self.dashboard.runtime_header.render_replay),
        )
        self._presentation = PresentationCoordinator(
            (
                DashboardPresenter(
                    self.dashboard,
                    RenderAdapter(self.global_status.render_dashboard),
                ),
                OrdersPresenter(self.orders),
                PositionsPresenter(self.positions),
                self._timeline_presenter,
                self._decisions_presenter,
                PortfolioPresenter(
                    self.dashboard.portfolio_panel,
                    RenderAdapter(
                        self.dashboard.runtime_header.render_portfolio
                    ),
                ),
                HealthPresenter(
                    self.dashboard.operator_health_panel,
                    self.sidebar,
                    RenderAdapter(
                        self.dashboard.runtime_header.render_health
                    ),
                    RenderAdapter(self.global_status.render_health),
                ),
                self._watchlist_presenter,
                self._replay_presenter,
                RuntimeControlsPresenter(self.start_button, self.stop_button),
                RuntimeStatusPresenter(self.status_label),
                RuntimeErrorPresenter(self),
            )
        )
        for activity in (
            self.activity,
            self.dashboard.activity_panel,
        ):
            activity.filters_changed.connect(
                self._timeline_presenter.set_filters
            )
        for decisions in (
            self.decisions,
            self.dashboard.decisions_panel,
        ):
            decisions.decision_selected.connect(
                self._decisions_presenter.select_decision
            )
        self.watchlist.sort_requested.connect(
            self._watchlist_presenter.sort_by
        )

    def _toggle_replay(self) -> None:
        workspace = self._replay_workspace
        if workspace is None:
            return
        if workspace.replay_state.active:
            workspace.pause()
        else:
            workspace.play()

    def _emergency_stop(self) -> None:
        self._runtime_service.stop()
        self.statusBar().showMessage(
            "Emergency stop requested. Runtime shutdown in progress.",
            5000,
        )

    def _render_state(self, state: ApplicationState) -> None:
        self._presentation.render(state)

    def _wire_replay_workspace(self, workspace: ReplayWorkspace) -> None:
        self.replay.play_requested.connect(workspace.play)
        self.replay.pause_requested.connect(workspace.pause)
        self.replay.step_requested.connect(workspace.step)
        self.replay.restart_requested.connect(workspace.restart)
        self.replay.timestamp_requested.connect(workspace.jump_to_timestamp)
        self.replay.event_index_requested.connect(
            workspace.jump_to_event_index
        )
        self._replay_state_bridge = QtReplayStateBridge(workspace, self)
        self._replay_state_bridge.state_changed.connect(self._render_state)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._runtime_service.close(timeout_seconds=5.0):
            QMessageBox.warning(
                self,
                "Runtime Still Stopping",
                "Stop the runtime and wait for shutdown before closing.",
            )
            event.ignore()
            return
        if self._replay_state_bridge is not None:
            self._replay_state_bridge.close()
        self._state_bridge.close()
        event.accept()


def _application_version() -> str:
    try:
        return version("webull-ai-trader")
    except PackageNotFoundError:
        return "0.1.0"

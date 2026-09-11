from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QMenu,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.gui.design.theme import application_stylesheet
from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.crypto_research import CryptoResearchPage
from app.gui.pages.orders import OrdersPage
from app.gui.pages.placeholder import PlaceholderPage
from app.gui.pages.replay import ReplayPage
from app.gui.presenters import (
    DashboardPresenter,
    ChartPresenter,
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
from app.services.chart_market_data import ChartMarketDataService
from app.gui.formatters.warrior_paper import format_warrior_paper


class MainWindow(QMainWindow):
    _PRIMARY_LAYOUT_VERSION = 4
    _SCANNER_HEADER_LAYOUT_VERSION = 2

    def __init__(
        self,
        bus: OperationsBus,
        state_store: ApplicationStateStore,
        runtime_service: RuntimeService,
        trading_service: TradingService | None = None,
        order_command_factory: OrderCommandFactory | None = None,
        replay_workspace: ReplayWorkspace | None = None,
        chart_market_data_service: ChartMarketDataService | None = None,
        chart_default_symbol: str | None = None,
        warrior_forward_sidecar=None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._state_store = state_store
        self._runtime_service = runtime_service
        self._trading_service = trading_service
        self._order_command_factory = order_command_factory
        self._replay_workspace = replay_workspace
        self._replay_state_bridge: QtReplayStateBridge | None = None
        self._chart_market_data_service = chart_market_data_service
        self._chart_default_symbol = chart_default_symbol
        self._chart_presenter: ChartPresenter | None = None
        self._warrior_forward_sidecar = warrior_forward_sidecar
        self._settings = settings or QSettings("Atlas", "WebullAITrader")
        self._sidebar_user_compact = False
        self._close_requested = False
        self._state_bridge = QtStateBridge(state_store, self)
        self._state_bridge.state_changed.connect(self._render_state)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Atlas \u2014 WebullAITrader")
        # The dashboard owns vertical overflow, so laptop-height windows do not
        # need to be artificially enlarged beyond the available screen.
        self.setMinimumSize(1024, 640)
        self.resize(1440, 900)
        self._build()
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(250)
        self._layout_save_timer.timeout.connect(self._save_layout)
        for splitter in (
            self.dashboard.market_workspace.middle_splitter,
            self.dashboard.market_workspace.right_splitter,
            self.dashboard.market_workspace.workspace_splitter,
            self.dashboard.market_workspace.lower_splitter,
        ):
            splitter.splitterMoved.connect(self._schedule_layout_save)
        for table in (
            self.watchlist._table,
            self.dashboard.market_workspace.watchlist._table,
        ):
            table.horizontalHeader().sectionResized.connect(
                self._schedule_layout_save
            )
        self._restore_layout()
        self._build_presentation()
        self.setStyleSheet(application_stylesheet())
        self._render_state(state_store.snapshot())
        self._warrior_refresh_timer = QTimer(self)
        self._warrior_refresh_timer.setInterval(1000)
        self._warrior_refresh_timer.timeout.connect(self._refresh_warrior_paper)
        self._warrior_refresh_timer.start()
        self._refresh_warrior_paper()
        if replay_workspace is not None:
            self._wire_replay_workspace(replay_workspace)
            self._render_replay_state(replay_workspace.state)
        self.sidebar.buttons["Dashboard"].setFocus()

    def resizeEvent(self, event: QResizeEvent) -> None:
        # Recover workspace width on common laptop displays while retaining
        # every navigation destination through labeled tooltips/accessibility.
        if hasattr(self, "sidebar"):
            self.sidebar.set_compact(
                event.size().width() <= 1366 or self._sidebar_user_compact
            )
        if hasattr(self, "dashboard"):
            self.dashboard.set_viewport_width(event.size().width())
        super().resizeEvent(event)
        self._schedule_layout_save()

    def _schedule_layout_save(self, *_args) -> None:
        timer = getattr(self, "_layout_save_timer", None)
        if timer is not None and not getattr(self, "_restoring_layout", False):
            timer.start()

    def _refresh_warrior_paper(self) -> None:
        sidecar = self._warrior_forward_sidecar
        if sidecar is None:
            return
        self.dashboard.market_workspace.render_warrior(
            format_warrior_paper(sidecar.snapshot())
        )
        sidecar.mark_gui_refresh()

    def _warrior_management_context(self, symbol: str):
        sidecar = self._warrior_forward_sidecar
        if sidecar is None:
            return None
        source = getattr(sidecar, "management_context", None)
        return source(symbol) if callable(source) else None

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
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self.pages = QStackedWidget()
        self.dashboard = DashboardPage()
        self.dashboard.workstation_footer.set_value(
            "Version", _application_version()
        )
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
        self.scanner_research_tabs = QTabWidget()
        self.scanner_research_tabs.setObjectName("scannerResearchTabs")
        self.scanner_research_tabs.addTab(self.watchlist, "Equity Scanner")
        self.crypto_research = CryptoResearchPage()
        self.scanner_research_tabs.addTab(self.crypto_research, "Crypto Research")
        self.pages.addWidget(self.scanner_research_tabs)  # 7
        self.replay = ReplayPage()
        self.pages.addWidget(self.replay)  # 8
        self.operations = self.dashboard.operator_workspace
        self.pages.addWidget(self.operations)  # 9
        content_layout.addWidget(self.pages, 1)
        outer.addWidget(content, 1)
        self.sidebar.page_requested.connect(self.pages.setCurrentIndex)
        self.pages.currentChanged.connect(self.sidebar.set_current_page)
        self.sidebar.compact_toggled.connect(self._set_sidebar_compact)
        self.setCentralWidget(root)

        header = self.dashboard.runtime_header
        controls = self.dashboard.market_workspace.runtime_controls
        self.start_button = controls.start_button
        self.stop_button = controls.stop_button
        self.pause_button = header.pause_button
        self.emergency_button = controls.emergency_stop_button
        self.start_button.clicked.connect(self._runtime_service.start)
        self.stop_button.clicked.connect(
            lambda checked=False: self._runtime_service.stop()
        )
        self.pause_button.clicked.connect(self._toggle_replay)
        header.reset_layout_requested.connect(self.reset_layout)
        header.settings_requested.connect(lambda: self.pages.setCurrentIndex(4))
        header.menu_requested.connect(self._show_menu)
        # Persistent navigation supersedes the former dashboard-only controls.
        header.settings_button.hide()
        header.menu_button.hide()
        controls.emergency_stop_requested.connect(self._emergency_stop)

        status = QStatusBar()
        self.global_status = GlobalStatusBar(version=_application_version())
        status.addWidget(self.global_status, 1)
        self.status_label = QLabel()
        self.status_label.setVisible(False)
        status.addWidget(self.status_label)
        self.setStatusBar(status)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        for label, index in zip(self.sidebar.ITEMS, self.sidebar.ROUTES):
            action = menu.addAction(label)
            action.triggered.connect(lambda _checked=False, page=index: self.pages.setCurrentIndex(page))
        menu.exec(self.dashboard.runtime_header.menu_button.mapToGlobal(self.dashboard.runtime_header.menu_button.rect().bottomLeft()))

    def _build_presentation(self) -> None:
        self._timeline_presenter = TimelinePresenter(
            self.activity, self.dashboard.operations_activity_panel,
        )
        self._decisions_presenter = DecisionsPresenter(
            self.decisions,
            self.dashboard.decisions_panel,
        )
        self._watchlist_presenter = WatchlistPresenter(
            self.watchlist,
            self.dashboard.market_workspace,
            RenderAdapter(self.dashboard.runtime_header.render_watchlist),
        )
        # Chart services remain implemented for dedicated consumers, but the
        # primary autonomous workstation no longer requests or renders charts.
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
                    management_context_source=self._warrior_management_context,
                ),
                OrdersPresenter(self.orders),
                PositionsPresenter(self.positions, self._warrior_management_context),
                self._timeline_presenter,
                self._decisions_presenter,
                PortfolioPresenter(
                    self.dashboard.portfolio_panel,
                    self.dashboard.operator_workspace.portfolio_intelligence,
                    RenderAdapter(
                        self.dashboard.runtime_header.render_portfolio
                    ),
                ),
                HealthPresenter(
                    self.dashboard.operator_health_panel,
                    self.sidebar,
                    self.dashboard.infrastructure,
                    RenderAdapter(
                        self.dashboard.runtime_header.render_health
                    ),
                    RenderAdapter(self.global_status.render_health),
                ),
                self._watchlist_presenter,
                *(
                    (self._chart_presenter,)
                    if self._chart_presenter is not None
                    else ()
                ),
                self._replay_presenter,
                RuntimeControlsPresenter(
                    self.start_button,
                    self.stop_button,
                    self.dashboard.market_workspace.runtime_controls,
                ),
                RuntimeStatusPresenter(self.status_label),
                RuntimeErrorPresenter(self),
            )
        )
        for activity in (self.activity, self.dashboard.operations_activity_panel):
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

    def _set_sidebar_compact(self, compact: bool) -> None:
        # The laptop breakpoint is mandatory; the preference applies on wider
        # workstations where either rail style is usable.
        self._sidebar_user_compact = bool(compact)
        self.sidebar.set_compact(
            self.width() <= 1366 or self._sidebar_user_compact
        )

    def _restore_layout(self) -> None:
        self._restoring_layout = True
        settings = self._settings
        geometry = settings.value("layout/window_geometry")
        restored_geometry = (
            isinstance(geometry, QByteArray)
            and not geometry.isEmpty()
            and self.restoreGeometry(geometry)
        )
        if restored_geometry:
            restored_geometry = any(
                screen.availableGeometry().intersects(self.frameGeometry())
                for screen in QGuiApplication.screens()
            )
        if not restored_geometry:
            self.resize(1440, 900)

        self._sidebar_user_compact = _setting_bool(
            settings.value("layout/sidebar_compact", False)
        )
        self.sidebar.set_compact(
            self.width() <= 1366 or self._sidebar_user_compact
        )
        self.dashboard.set_viewport_width(self.width(), force=True)

        workspace = self.dashboard.market_workspace
        for key, version_key, table in (
            ("layout/scanner_header", "layout/scanner_header_version", self.watchlist._table),
            ("layout/dashboard_scanner_header", "layout/dashboard_scanner_header_version", workspace.watchlist._table),
        ):
            header_state = settings.value(key)
            version = settings.value(version_key, 0, type=int)
            if (
                version == self._SCANNER_HEADER_LAYOUT_VERSION
                and isinstance(header_state, QByteArray)
                and not header_state.isEmpty()
            ):
                self._restore_scanner_header(table, header_state)
        saved_mode = settings.value("layout/responsive_mode", "")
        if saved_mode == workspace.layout_mode:
            splitter_states = [
                ("layout/right_rail_splitter", workspace.right_splitter),
            ]
            if (
                settings.value("layout/primary_layout_version", 0, type=int)
                == self._PRIMARY_LAYOUT_VERSION
            ):
                splitter_states.insert(
                    0,
                    ("layout/chart_scanner_splitter", workspace.middle_splitter),
                )
            for key, splitter in splitter_states:
                state = settings.value(key)
                if isinstance(state, QByteArray) and not state.isEmpty():
                    splitter.restoreState(state)
        # Splitter state is applied before the native window has its final
        # geometry. Validate again on the next event-loop turn so an old or
        # collapsed production state cannot leave Trade Intelligence at 0 px.
        QTimer.singleShot(0, workspace.ensure_middle_composition)
        self._restoring_layout = False

    def _restore_scanner_header(self, table, state: QByteArray) -> None:
        header = table.horizontalHeader()
        if not header.restoreState(state):
            return
        # A valid serialized state can still be structurally stale (for
        # example, all columns saved at their old narrow widths). Reject only
        # states that visibly fail to use the current viewport; user sizing
        # remains persistent for sane states.
        viewport_width = table.viewport().width()
        if viewport_width > 200 and header.length() < round(viewport_width * 0.80):
            resetter = getattr(table.parentWidget(), "reset_header_layout", None)
            if callable(resetter):
                resetter()

    def _save_layout(self) -> None:
        workspace = self.dashboard.market_workspace
        settings = self._settings
        settings.setValue("layout/window_geometry", self.saveGeometry())
        settings.setValue("layout/responsive_mode", workspace.layout_mode or "")
        settings.setValue(
            "layout/primary_layout_version", self._PRIMARY_LAYOUT_VERSION
        )
        settings.setValue("layout/chart_scanner_splitter", workspace.splitter.saveState())
        settings.setValue("layout/right_rail_splitter", workspace.right_splitter.saveState())
        settings.setValue("layout/sidebar_compact", self._sidebar_user_compact)
        settings.setValue(
            "layout/scanner_header",
            self.watchlist._table.horizontalHeader().saveState(),
        )
        settings.setValue(
            "layout/scanner_header_version", self._SCANNER_HEADER_LAYOUT_VERSION,
        )
        settings.setValue(
            "layout/dashboard_scanner_header",
            workspace.watchlist._table.horizontalHeader().saveState(),
        )
        settings.setValue(
            "layout/dashboard_scanner_header_version",
            self._SCANNER_HEADER_LAYOUT_VERSION,
        )
        settings.sync()

    def reset_layout(self) -> None:
        self._settings.remove("layout")
        self._sidebar_user_compact = False
        self.resize(1440, 900)
        self.sidebar.set_compact(False)
        self.dashboard.market_workspace.reset_layout(self.width())
        self._schedule_layout_save()

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
        self.crypto_research.set_runtime_phase(state.runtime.phase)
        self._presentation.render(state)

    def _render_replay_state(self, state: ApplicationState) -> None:
        self._replay_presenter.render(state)

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
        self._replay_state_bridge.state_changed.connect(
            self._render_replay_state
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        # Joining the runtime here blocks the Qt event loop exactly when a busy
        # market-data worker most needs a responsive shutdown path. Request a
        # cooperative stop and poll status through the event loop instead.
        if self._runtime_service.is_active:
            if not self._close_requested:
                self._close_requested = True
                self._runtime_service.stop("Application shutdown requested.")
            event.ignore()
            QTimer.singleShot(50, self.close)
            return
        self._warrior_refresh_timer.stop()
        if self._chart_presenter is not None:
            self._chart_presenter.close()
        if self._replay_state_bridge is not None:
            self._replay_state_bridge.close()
        self._save_layout()
        self._state_bridge.close()
        event.accept()


def _application_version() -> str:
    try:
        return version("webull-ai-trader")
    except PackageNotFoundError:
        return "0.1.0"


def _setting_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

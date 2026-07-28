from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, QSettings, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.gui.components.statusbar import PersistentStatusBar
from app.gui.theme import Sizing, Spacing, application_stylesheet
from app.gui.event_store_bridge import QtEventStoreBridge
from app.gui.analytics_bridge import QtAnalyticsBridge
from app.gui.backtesting_bridge import QtBacktestingBridge
from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.pages.placeholder import PlaceholderPage
from app.gui.projections.dashboard_projection import project_dashboard
from app.gui.replay_bridge import QtReplayBridge
from app.gui.recording_bridge import QtRecordingBridge
from app.gui.shell.sidebar import Sidebar
from app.gui.state_bridge import QtStateBridge
from app.operations_core import ApplicationState, ApplicationStateStore, OperationsBus, OperatorSelectionEvent, RuntimePhase
from app.services import RuntimeService
from app.read_models.decisions import DecisionProjector
from app.read_models.operator_workspace import OperatorWorkspaceProjector
from app.read_models.runtime_health import RuntimeHealthProjector
from app.read_models.timeline import TimelineProjector
from app.read_models.trade_lifecycle import TradeLifecycleProjector
from app.composition.desktop import ReplayProjectionGraph
from app.replay import (
    ReplayController,
    ReplaySnapshot,
    ReplayState,
    ReplayStatus,
)
from app.recording import RecordingController, RecordingSnapshot
from app.recording import RecordingStatus
from app.event_store import EventStoreController, EventStoreSnapshot
from app.analytics import (
    AnalyticsController,
    AnalyticsSnapshot,
)
from app.backtesting.controller import BacktestingController
from app.backtesting.models import (
    BacktestConfiguration,
    Experiment,
    ExperimentSnapshot,
)


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
        operator_workspace_projector: OperatorWorkspaceProjector,
        replay_controller: ReplayController,
        replay_projections: ReplayProjectionGraph,
        recording_controller: RecordingController,
        event_store_controller: EventStoreController,
        analytics_controller: AnalyticsController,
        backtesting_controller: BacktestingController,
        *,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._state_store = state_store
        self._runtime_service = runtime_service
        self._decision_projector = decision_projector
        self._runtime_health_projector = runtime_health_projector
        self._timeline_projector = timeline_projector
        self._trade_lifecycle_projector = trade_lifecycle_projector
        self._operator_workspace_projector = operator_workspace_projector
        self._replay_controller = replay_controller
        self._replay_projections = replay_projections
        self._recording_controller = recording_controller
        self._event_store_controller = event_store_controller
        self._analytics_controller = analytics_controller
        self._backtesting_controller = backtesting_controller
        self._settings = (
            settings
            if settings is not None
            else QSettings("Webull AI Trader", "Atlas X")
        )
        self._last_error = ""
        self._state_bridge = QtStateBridge(state_store, self)
        self._state_bridge.state_changed.connect(self._render_state)
        self._replay_bridge = QtReplayBridge(replay_controller, self)
        self._replay_bridge.replay_changed.connect(
            self._render_replay
        )
        self._recording_bridge = QtRecordingBridge(
            recording_controller,
            self,
        )
        self._recording_bridge.recording_changed.connect(
            self._render_recording
        )
        self._event_store_bridge = QtEventStoreBridge(
            event_store_controller,
            self,
        )
        self._event_store_bridge.event_store_changed.connect(
            self._render_event_store
        )
        self._analytics_bridge = QtAnalyticsBridge(
            analytics_controller,
            self,
        )
        self._analytics_bridge.analytics_changed.connect(
            self._render_analytics
        )
        self._backtesting_bridge = QtBacktestingBridge(
            backtesting_controller,
            self,
        )
        self._backtesting_bridge.experiments_changed.connect(
            self._render_experiments
        )
        self._replay_timer = QTimer(self)
        self._replay_timer.setInterval(100)
        self._replay_timer.timeout.connect(self._advance_replay)
        self._replay_timer.start()
        self.setWindowTitle("Atlas X — WebullAITrader")
        self.setMinimumSize(
            Sizing.WINDOW_MIN_WIDTH,
            Sizing.WINDOW_MIN_HEIGHT,
        )
        self._build()
        self.setStyleSheet(application_stylesheet())
        self._restore_workspace_state()
        self._render_state(state_store.snapshot())

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.shell_splitter = QSplitter()
        self.shell_splitter.setObjectName("shellSplitter")
        self.shell_splitter.setChildrenCollapsible(False)
        self.sidebar = Sidebar()
        self.shell_splitter.addWidget(self.sidebar)

        content = QWidget()
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(
            Spacing.LG,
            Spacing.MD,
            Spacing.LG,
            Spacing.MD,
        )
        content_layout.setSpacing(Spacing.MD)

        self.pages = QStackedWidget()
        self.dashboard = DashboardPage()
        self.dashboard.start_runtime_requested.connect(
            self._runtime_service.start
        )
        self.dashboard.stop_runtime_requested.connect(
            self._runtime_service.stop
        )
        self.dashboard.selection_requested.connect(
            self._publish_operator_selection
        )
        self.dashboard.replay_play_requested.connect(
            self._replay_controller.play
        )
        self.dashboard.replay_pause_requested.connect(
            self._replay_controller.pause
        )
        self.dashboard.replay_stop_requested.connect(
            self._replay_controller.stop
        )
        self.dashboard.replay_step_forward_requested.connect(
            self._replay_controller.step_forward
        )
        self.dashboard.replay_step_backward_requested.connect(
            self._replay_controller.step_backward
        )
        self.dashboard.replay_jump_requested.connect(
            self._replay_controller.seek
        )
        self.dashboard.replay_speed_requested.connect(
            self._replay_controller.set_speed
        )
        self.dashboard.recording_open_requested.connect(
            self._open_recording
        )
        self.dashboard.recording_save_requested.connect(
            self._save_recording
        )
        self.dashboard.event_store_query_requested.connect(
            self._query_event_store
        )
        self.dashboard.event_store_replay_requested.connect(
            self._event_store_controller.open_replay
        )
        self.dashboard.event_store_refresh_requested.connect(
            self._event_store_controller.refresh
        )
        self.dashboard.experiment_start_requested.connect(
            self._start_experiment
        )
        self.dashboard.experiment_pause_requested.connect(
            self._backtesting_controller.pause
        )
        self.dashboard.experiment_resume_requested.connect(
            self._backtesting_controller.resume
        )
        self.dashboard.experiment_step_requested.connect(
            self._backtesting_controller.step
        )
        self.dashboard.experiment_stop_requested.connect(
            self._backtesting_controller.stop
        )
        self.dashboard.experiment_compare_requested.connect(
            self._backtesting_controller.compare
        )
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
        self.shell_splitter.addWidget(content)
        self.shell_splitter.setStretchFactor(0, 0)
        self.shell_splitter.setStretchFactor(1, 1)
        self.shell_splitter.setSizes([190, 1310])
        outer.addWidget(self.shell_splitter, 1)
        self.sidebar.page_requested.connect(self.pages.setCurrentIndex)
        self.setCentralWidget(root)

        self.workspace_splitter = self.dashboard.workspace_splitter
        self.start_button = self.dashboard.runtime_ribbon.start_button
        self.stop_button = self.dashboard.runtime_ribbon.stop_button
        self.persistent_status_bar = PersistentStatusBar()
        self.status_label = self.persistent_status_bar.runtime
        self.setStatusBar(self.persistent_status_bar)

    def _emergency_stop(self) -> None:
        self._runtime_service.stop()
        self.statusBar().showMessage("Emergency stop requested. Runtime shutdown in progress.", 5000)

    def _render_state(self, state: ApplicationState) -> None:
        replay = self._replay_controller.snapshot()
        if replay.state is ReplayState.REPLAY:
            source_state = self._replay_projections.state_store.snapshot()
            decision_projector = (
                self._replay_projections.decision_projector
            )
            health_projector = (
                self._replay_projections.runtime_health_projector
            )
            timeline_projector = (
                self._replay_projections.timeline_projector
            )
            lifecycle_projector = (
                self._replay_projections.trade_lifecycle_projector
            )
            workspace_projector = (
                self._replay_projections.operator_workspace_projector
            )
        else:
            source_state = state
            decision_projector = self._decision_projector
            health_projector = self._runtime_health_projector
            timeline_projector = self._timeline_projector
            lifecycle_projector = self._trade_lifecycle_projector
            workspace_projector = self._operator_workspace_projector
        dashboard_snapshot = project_dashboard(
            source_state,
            decision_projector.snapshot(),
            health_projector.snapshot(),
            timeline_projector.snapshot(),
            lifecycle_projector.snapshot(),
            workspace_projector.snapshot(),
            replay,
            self._recording_controller.snapshot(),
            self._event_store_controller.snapshot(),
            self._analytics_controller.snapshot(),
            self._backtesting_controller.snapshot(),
        )
        self.dashboard.render(dashboard_snapshot)
        phase = state.runtime.phase
        active = phase in {RuntimePhase.STARTING, RuntimePhase.RUNNING, RuntimePhase.STOPPING}
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(phase in {RuntimePhase.STARTING, RuntimePhase.RUNNING})
        self.persistent_status_bar.set_runtime(phase.value)
        recording = self._recording_controller.snapshot()
        self.persistent_status_bar.set_recorder(
            recording.status.value
        )
        self.persistent_status_bar.set_events_per_second(
            "0"
        )
        if phase is RuntimePhase.FAILED and state.runtime.last_error and state.runtime.last_error != self._last_error:
            self._last_error = state.runtime.last_error
            QMessageBox.critical(self, "Runtime Error", state.runtime.last_error)

    def _publish_operator_selection(self, event: object) -> None:
        if not isinstance(event, OperatorSelectionEvent):
            raise TypeError(
                "selection event must be an OperatorSelectionEvent"
            )
        if (
            self._replay_controller.snapshot().state
            is ReplayState.REPLAY
        ):
            self._replay_projections.bus.publish(event)
            self._render_state(self._state_store.snapshot())
        else:
            self._bus.publish(event)

    def _render_replay(self, snapshot: ReplaySnapshot) -> None:
        del snapshot
        self._render_state(self._state_store.snapshot())

    def _render_recording(
        self,
        snapshot: RecordingSnapshot,
    ) -> None:
        if snapshot.status is RecordingStatus.COMPLETED:
            self._event_store_controller.refresh()
        self._render_state(self._state_store.snapshot())

    def _render_event_store(
        self,
        snapshot: EventStoreSnapshot,
    ) -> None:
        del snapshot
        self._analytics_controller.refresh()
        self._render_state(self._state_store.snapshot())

    def _render_analytics(
        self,
        snapshot: AnalyticsSnapshot,
    ) -> None:
        del snapshot
        self._render_state(self._state_store.snapshot())

    def _render_experiments(
        self,
        snapshot: ExperimentSnapshot,
    ) -> None:
        del snapshot
        self._render_state(self._state_store.snapshot())

    def _start_experiment(
        self,
        experiment_id: str,
        name: str,
        strategy_version: str,
    ) -> None:
        try:
            self._backtesting_controller.start_experiment(
                Experiment(
                    experiment_id,
                    name,
                    BacktestConfiguration(strategy_version),
                )
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "Experiment Error", str(exc))

    def _query_event_store(self, method: str, value: object) -> None:
        if method == "all":
            self._event_store_controller.query("all")
        elif isinstance(value, str):
            self._event_store_controller.query(method, value)

    def _open_recording(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Atlas Recording",
            "",
            "Atlas Session (*.atlas-session.json);;JSON (*.json)",
        )
        if not path:
            return
        try:
            self._recording_controller.open(Path(path))
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(
                self,
                "Recording Error",
                str(exc),
            )

    def _save_recording(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Atlas Recording",
            "session.atlas-session.json",
            "Atlas Session (*.atlas-session.json);;JSON (*.json)",
        )
        if not path:
            return
        try:
            self._recording_controller.save(Path(path))
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.critical(
                self,
                "Recording Error",
                str(exc),
            )

    def _advance_replay(self) -> None:
        if (
            self._replay_controller.snapshot().status
            is ReplayStatus.PLAYING
        ):
            self._replay_controller.advance(Decimal("0.1"))

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._runtime_service.close(timeout_seconds=5.0):
            QMessageBox.warning(self, "Runtime Still Stopping", "Stop the runtime and wait for shutdown before closing.")
            event.ignore()
            return
        self._state_bridge.close()
        self._replay_bridge.close()
        self._recording_bridge.close()
        self._event_store_bridge.close()
        self._analytics_bridge.close()
        self._backtesting_bridge.close()
        self._replay_timer.stop()
        self._save_workspace_state()
        event.accept()

    def _restore_workspace_state(self) -> None:
        geometry = self._settings.value("window/geometry")
        restored_geometry = (
            isinstance(geometry, QByteArray)
            and self.restoreGeometry(geometry)
        )
        if not restored_geometry or not self._is_on_screen():
            self._apply_default_geometry()

        window_state = self._settings.value("window/state")
        if isinstance(window_state, QByteArray):
            self.restoreState(window_state)

        shell_state = self._settings.value("splitters/shell")
        if isinstance(shell_state, QByteArray):
            self.shell_splitter.restoreState(shell_state)
        workspace_state = self._settings.value(
            "splitters/workspace"
        )
        if isinstance(workspace_state, QByteArray):
            self.workspace_splitter.restoreState(workspace_state)

        collapsed = self._settings.value(
            "sidebar/collapsed",
            False,
            type=bool,
        )
        self.sidebar.set_collapsed(bool(collapsed))
        page_index = self._settings.value(
            "sidebar/page",
            0,
            type=int,
        )
        if 0 <= page_index < self.pages.count():
            self.pages.setCurrentIndex(page_index)
            self.sidebar.set_current_index(page_index)

    def _save_workspace_state(self) -> None:
        self._settings.setValue(
            "window/geometry",
            self.saveGeometry(),
        )
        self._settings.setValue("window/state", self.saveState())
        self._settings.setValue(
            "splitters/shell",
            self.shell_splitter.saveState(),
        )
        self._settings.setValue(
            "splitters/workspace",
            self.workspace_splitter.saveState(),
        )
        self._settings.setValue(
            "sidebar/collapsed",
            self.sidebar.is_collapsed,
        )
        self._settings.setValue(
            "sidebar/page",
            self.pages.currentIndex(),
        )
        self._settings.sync()

    def _is_on_screen(self) -> bool:
        frame = self.frameGeometry()
        return any(
            screen.availableGeometry().intersects(frame)
            for screen in QGuiApplication.screens()
        )

    def _apply_default_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(
                Sizing.WINDOW_DEFAULT_WIDTH,
                Sizing.WINDOW_DEFAULT_HEIGHT,
            )
            return
        available = screen.availableGeometry()
        width = min(
            Sizing.WINDOW_DEFAULT_WIDTH,
            available.width(),
        )
        height = min(
            Sizing.WINDOW_DEFAULT_HEIGHT,
            available.height(),
        )
        self.setGeometry(
            QRect(
                available.x()
                + (available.width() - width) // 2,
                available.y()
                + (available.height() - height) // 2,
                width,
                height,
            )
        )


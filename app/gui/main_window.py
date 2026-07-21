from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services import RuntimeService
from app.gui.state_bridge import QtStateBridge
from app.operations_core import (
    ApplicationState,
    ApplicationStateStore,
    OperationsBus,
    RuntimePhase,
)


class StatusCard(QFrame):
    def __init__(self, title: str, value: str) -> None:
        super().__init__()

        self.setObjectName("statusCard")

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")

        self._value = QLabel(value)
        self._value.setObjectName("cardValue")
        self._value.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(title_label)
        layout.addWidget(self._value)
        layout.addStretch()

    def set_value(self, value: str) -> None:
        self._value.setText(value)


class MainWindow(QMainWindow):
    def __init__(
        self,
        bus: OperationsBus,
        state_store: ApplicationStateStore,
        runtime_service: RuntimeService,
) -> None:
        super().__init__()

        self._bus = bus
        self._state_store = state_store
        self._runtime_service = runtime_service
        self._state = state_store.snapshot()

        self._state_bridge = QtStateBridge(state_store, self)
        self._state_bridge.state_changed.connect(self._render_state)

        self.setWindowTitle("Webull AI Trader   Operations Center")
        self.setMinimumSize(1050, 720)

        self._build_interface()
        self._apply_theme()
        self._render_state(self._state)

    def _build_interface(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(18)

        header_layout = QHBoxLayout()
        title_column = QVBoxLayout()

        title = QLabel("WEBULL AI TRADER")
        title.setObjectName("applicationTitle")

        subtitle = QLabel("Operations Center")
        subtitle.setObjectName("applicationSubtitle")

        title_column.addWidget(title)
        title_column.addWidget(subtitle)

        self._mode_badge = QLabel("PAPER")
        self._mode_badge.setObjectName("modeBadge")
        self._mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_badge.setFixedSize(100, 34)

        header_layout.addLayout(title_column)
        header_layout.addStretch()
        header_layout.addWidget(self._mode_badge)

        root_layout.addLayout(header_layout)

        controls = QHBoxLayout()

        self._start_button = QPushButton("?  Start Paper Runtime")
        self._start_button.setObjectName("startButton")
        self._start_button.clicked.connect(self._start_runtime)

        self._stop_button = QPushButton("   Stop Runtime")
        self._stop_button.setObjectName("stopButton")
        self._stop_button.clicked.connect(self._stop_runtime)

        controls.addWidget(self._start_button)
        controls.addWidget(self._stop_button)
        controls.addStretch()

        root_layout.addLayout(controls)

        cards = QGridLayout()
        cards.setHorizontalSpacing(14)
        cards.setVerticalSpacing(14)

        self._runtime_card = StatusCard("RUNTIME", "Stopped")
        self._broker_card = StatusCard("BROKER", "Disconnected")
        self._market_card = StatusCard("MARKET FEED", "Idle")
        self._model_card = StatusCard("ACTIVE MODEL", "Not loaded")
        self._inference_card = StatusCard("INFERENCE", "Ready")
        self._emergency_card = StatusCard("EMERGENCY STOP", "Active")
        self._cycle_card = StatusCard("RUNTIME CYCLES", "0")
        self._environment_card = StatusCard("ENVIRONMENT", "PAPER")

        cards.addWidget(self._runtime_card, 0, 0)
        cards.addWidget(self._broker_card, 0, 1)
        cards.addWidget(self._market_card, 0, 2)
        cards.addWidget(self._model_card, 0, 3)
        cards.addWidget(self._inference_card, 1, 0)
        cards.addWidget(self._emergency_card, 1, 1)
        cards.addWidget(self._cycle_card, 1, 2)
        cards.addWidget(self._environment_card, 1, 3)

        root_layout.addLayout(cards)

        activity_panel = QFrame()
        activity_panel.setObjectName("activityPanel")

        activity_layout = QVBoxLayout(activity_panel)

        activity_title = QLabel("MISSION TIMELINE")
        activity_title.setObjectName("sectionTitle")

        self._activity_label = QLabel("No operations events recorded.")
        self._activity_label.setObjectName("activityText")
        self._activity_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._activity_label.setWordWrap(True)

        activity_layout.addWidget(activity_title)
        activity_layout.addWidget(self._activity_label)
        activity_layout.addStretch()

        root_layout.addWidget(activity_panel, 1)

        self._status_bar_label = QLabel()
        self._status_bar_label.setObjectName("operationsStatusBar")
        self._status_bar_label.setWordWrap(True)
        root_layout.addWidget(self._status_bar_label)

        safety_notice = QLabel(
            "READ-ONLY OPERATIONS CENTER   no live orders, cancellations, "
            "replacements, or broker mutations are available."
        )
        safety_notice.setObjectName("safetyNotice")
        safety_notice.setWordWrap(True)

        root_layout.addWidget(safety_notice)

        self.setCentralWidget(root)

    def _start_runtime(self) -> None:
        self._runtime_service.start()

    def _stop_runtime(self) -> None:
        self._runtime_service.stop()

    def _render_state(self, state: ApplicationState) -> None:
        self._state = state
        runtime = state.runtime

        self._runtime_card.set_value(runtime.phase.value.title())
        self._broker_card.set_value(runtime.broker_status)
        self._market_card.set_value(runtime.market_feed_status)
        self._model_card.set_value(runtime.active_model)
        self._inference_card.set_value(runtime.inference_status)
        self._emergency_card.set_value("Active")
        self._cycle_card.set_value(str(runtime.cycles_completed))
        self._environment_card.set_value(runtime.environment)
        self._mode_badge.setText(runtime.environment)

        if state.timeline:
            recent_entries = state.timeline[-8:]
            timeline_text = "\n".join(
                (
                    f"{entry.occurred_at.astimezone():%H:%M:%S}  "
                    f"{entry.message}"
                )
                for entry in recent_entries
            )
        else:
            timeline_text = "No operations events recorded."

        self._activity_label.setText(timeline_text)

        running = runtime.phase in {
            RuntimePhase.STARTING,
            RuntimePhase.RUNNING,
            RuntimePhase.STOPPING,
        }

        self._start_button.setEnabled(not running)
        self._stop_button.setEnabled(
            runtime.phase in {
                RuntimePhase.STARTING,
                RuntimePhase.RUNNING,
            }
        )

        health = (
            "ERROR"
            if runtime.phase is RuntimePhase.FAILED
            else "HEALTHY"
        )

        self._status_bar_label.setText(
            f"{runtime.environment}     "
            f"Runtime: {runtime.phase.value}     "
            f"Market Feed: {runtime.market_feed_status}     "
            f"Model: {runtime.active_model}     "
            f"Cycles: {runtime.cycles_completed}     "
            f"Health: {health}"
        )

        if runtime.phase is RuntimePhase.FAILED and runtime.last_error:
            QMessageBox.critical(
                self,
                "Runtime Error",
                runtime.last_error,
            )


    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._runtime_service.close(timeout_seconds=5.0):
            QMessageBox.warning(
                self,
                "Runtime Still Stopping",
                "The runtime has not stopped yet. Use Stop Runtime and "
                "wait for shutdown before closing.",
            )
            event.ignore()
            return

        self._state_bridge.close()
        event.accept()

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #101318;
                color: #e7eaf0;
                font-family: "Segoe UI";
                font-size: 14px;
            }

            #applicationTitle {
                font-size: 27px;
                font-weight: 700;
                letter-spacing: 2px;
            }

            #applicationSubtitle {
                color: #9098a8;
                font-size: 14px;
            }

            #modeBadge {
                background: #123c2b;
                border: 1px solid #218c5a;
                border-radius: 8px;
                color: #71e0a5;
                font-weight: 700;
            }

            QPushButton {
                min-height: 42px;
                padding: 0 20px;
                border-radius: 7px;
                font-weight: 600;
            }

            #startButton {
                background: #16794c;
                border: 1px solid #249e68;
                color: white;
            }

            #startButton:hover {
                background: #1b8b59;
            }

            #startButton:disabled {
                background: #29332f;
                border-color: #39443f;
                color: #758079;
            }

            #stopButton {
                background: #842f39;
                border: 1px solid #a84854;
                color: white;
            }

            #stopButton:hover {
                background: #963642;
            }

            #stopButton:disabled {
                background: #37292c;
                border-color: #473438;
                color: #806f72;
            }

            #statusCard, #activityPanel {
                background: #181d24;
                border: 1px solid #2b323d;
                border-radius: 10px;
            }

            #cardTitle, #sectionTitle {
                color: #8993a4;
                font-size: 12px;
                font-weight: 700;
            }

            #cardValue {
                color: #f3f5f8;
                font-size: 20px;
                font-weight: 650;
            }

            #activityText {
                color: #c7cdd8;
                font-family: "Consolas";
                padding-top: 12px;
            }

            #operationsStatusBar {
                background: #151a20;
                border: 1px solid #2b323d;
                border-radius: 6px;
                color: #aab3c2;
                font-family: "Consolas";
                font-size: 12px;
                padding: 8px 12px;
            }

            #safetyNotice {
                background: #2e2715;
                border: 1px solid #665522;
                border-radius: 7px;
                color: #e4c768;
                padding: 10px;
                font-size: 12px;
            }
            """
        )









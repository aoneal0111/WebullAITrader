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

from app.gui.models import DashboardSnapshot, RuntimeState
from app.gui.runtime_worker import RuntimeWorker


class StatusCard(QFrame):
    def __init__(self, title: str, value: str) -> None:
        super().__init__()

        self.setObjectName("statusCard")

        self._title = QLabel(title)
        self._title.setObjectName("cardTitle")

        self._value = QLabel(value)
        self._value.setObjectName("cardValue")
        self._value.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._value)
        layout.addStretch()

    def set_value(self, value: str) -> None:
        self._value.setText(value)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self._worker: RuntimeWorker | None = None
        self._snapshot = DashboardSnapshot.initial()

        self.setWindowTitle("Webull AI Trader — Operations Center")
        self.setMinimumSize(1050, 680)

        self._build_interface()
        self._apply_theme()
        self._render_snapshot(self._snapshot)

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

        self._start_button = QPushButton("▶  Start Paper Runtime")
        self._start_button.setObjectName("startButton")
        self._start_button.clicked.connect(self._start_runtime)

        self._stop_button = QPushButton("■  Stop Runtime")
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

        activity_title = QLabel("SESSION ACTIVITY")
        activity_title.setObjectName("sectionTitle")

        self._activity_label = QLabel("Ready to start.")
        self._activity_label.setObjectName("activityText")
        self._activity_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._activity_label.setWordWrap(True)

        activity_layout.addWidget(activity_title)
        activity_layout.addWidget(self._activity_label)
        activity_layout.addStretch()

        root_layout.addWidget(activity_panel, 1)

        safety_notice = QLabel(
            "READ-ONLY GUI MILESTONE — no live orders, cancellations, "
            "replacements, or broker mutations are available."
        )
        safety_notice.setObjectName("safetyNotice")
        safety_notice.setWordWrap(True)

        root_layout.addWidget(safety_notice)

        self.setCentralWidget(root)

    def _start_runtime(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return

        self._worker = RuntimeWorker(self)
        self._worker.snapshot_changed.connect(self._render_snapshot)
        self._worker.runtime_failed.connect(self._handle_runtime_failure)
        self._worker.finished.connect(self._handle_worker_finished)
        self._worker.start()

        self._start_button.setEnabled(False)
        self._stop_button.setEnabled(True)

    def _stop_runtime(self) -> None:
        if self._worker is None or not self._worker.isRunning():
            return

        self._snapshot = DashboardSnapshot(
            environment=self._snapshot.environment,
            runtime_state=RuntimeState.STOPPING,
            broker_status=self._snapshot.broker_status,
            market_feed_status=self._snapshot.market_feed_status,
            inference_status=self._snapshot.inference_status,
            emergency_stop_enabled=self._snapshot.emergency_stop_enabled,
            active_model=self._snapshot.active_model,
            cycle_count=self._snapshot.cycle_count,
            status_message="Stopping paper runtime safely...",
        )
        self._render_snapshot(self._snapshot)

        self._stop_button.setEnabled(False)
        self._worker.requestInterruption()

    def _render_snapshot(self, snapshot: DashboardSnapshot) -> None:
        self._snapshot = snapshot

        self._runtime_card.set_value(snapshot.runtime_state.value.title())
        self._broker_card.set_value(snapshot.broker_status)
        self._market_card.set_value(snapshot.market_feed_status)
        self._model_card.set_value(snapshot.active_model)
        self._inference_card.set_value(snapshot.inference_status)
        self._emergency_card.set_value(
            "Active" if snapshot.emergency_stop_enabled else "Inactive"
        )
        self._cycle_card.set_value(str(snapshot.cycle_count))
        self._environment_card.set_value(snapshot.environment)
        self._activity_label.setText(snapshot.status_message)
        self._mode_badge.setText(snapshot.environment)

        running = snapshot.runtime_state in {
            RuntimeState.STARTING,
            RuntimeState.RUNNING,
            RuntimeState.STOPPING,
        }

        self._start_button.setEnabled(not running)
        self._stop_button.setEnabled(
            snapshot.runtime_state in {
                RuntimeState.STARTING,
                RuntimeState.RUNNING,
            }
        )

    def _handle_runtime_failure(self, message: str) -> None:
        self._render_snapshot(
            DashboardSnapshot(
                environment=self._snapshot.environment,
                runtime_state=RuntimeState.ERROR,
                broker_status="Disconnected",
                market_feed_status="Error",
                inference_status="Error",
                emergency_stop_enabled=True,
                active_model=self._snapshot.active_model,
                cycle_count=self._snapshot.cycle_count,
                status_message=f"Runtime error: {message}",
            )
        )

        QMessageBox.critical(self, "Runtime Error", message)

    def _handle_worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

        self._start_button.setEnabled(True)
        self._stop_button.setEnabled(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()

            if not self._worker.wait(5_000):
                QMessageBox.warning(
                    self,
                    "Runtime Still Stopping",
                    "The runtime has not stopped yet. Please use Stop Runtime "
                    "and wait for shutdown before closing.",
                )
                event.ignore()
                return

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
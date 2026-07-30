from __future__ import annotations

from datetime import UTC

from PySide6.QtCore import QDateTime, QTimeZone, Signal
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import ReplayWorkspaceSnapshot


class ReplayPage(QWidget):
    """Presentation-only operator surface for replay controls and status."""

    play_requested = Signal()
    pause_requested = Signal()
    step_requested = Signal()
    restart_requested = Signal()
    timestamp_requested = Signal(object)
    event_index_requested = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        title = QLabel("Replay Workspace")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.pause_button = QPushButton("Pause")
        self.step_button = QPushButton("Step")
        self.restart_button = QPushButton("Restart")
        for button in (
            self.play_button,
            self.pause_button,
            self.step_button,
            self.restart_button,
        ):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)

        seek_controls = QHBoxLayout()
        self.timestamp_input = QDateTimeEdit(
            QDateTime.currentDateTimeUtc()
        )
        self.timestamp_input.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.timestamp_input.setTimeZone(QTimeZone.utc())
        self.timestamp_button = QPushButton("Jump to Timestamp")
        self.event_index_input = QSpinBox()
        self.event_index_input.setRange(0, 0)
        self.event_index_button = QPushButton("Jump to Event")
        seek_controls.addWidget(self.timestamp_input)
        seek_controls.addWidget(self.timestamp_button)
        seek_controls.addWidget(self.event_index_input)
        seek_controls.addWidget(self.event_index_button)
        layout.addLayout(seek_controls)

        metrics = QFormLayout()
        self.status_value = QLabel()
        self.position_value = QLabel()
        self.events_processed_value = QLabel()
        self.total_events_value = QLabel()
        self.speed_value = QLabel()
        self.elapsed_value = QLabel()
        metrics.addRow("Status", self.status_value)
        metrics.addRow("Position", self.position_value)
        metrics.addRow("Events processed", self.events_processed_value)
        metrics.addRow("Total events", self.total_events_value)
        metrics.addRow("Replay speed", self.speed_value)
        metrics.addRow("Elapsed replay time", self.elapsed_value)
        layout.addLayout(metrics)
        layout.addStretch()

        self.play_button.clicked.connect(
            lambda checked=False: self.play_requested.emit()
        )
        self.pause_button.clicked.connect(
            lambda checked=False: self.pause_requested.emit()
        )
        self.step_button.clicked.connect(
            lambda checked=False: self.step_requested.emit()
        )
        self.restart_button.clicked.connect(
            lambda checked=False: self.restart_requested.emit()
        )
        self.timestamp_button.clicked.connect(self._request_timestamp)
        self.event_index_button.clicked.connect(
            lambda checked=False: self.event_index_requested.emit(
                self.event_index_input.value()
            )
        )

    def render(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        self.status_value.setText(snapshot.status)
        self.position_value.setText(snapshot.current_position)
        self.events_processed_value.setText(snapshot.events_processed)
        self.total_events_value.setText(snapshot.total_events)
        self.speed_value.setText(snapshot.replay_speed)
        self.elapsed_value.setText(snapshot.elapsed_time)
        self.play_button.setEnabled(snapshot.can_play)
        self.pause_button.setEnabled(snapshot.can_pause)
        self.step_button.setEnabled(snapshot.can_step)
        self.restart_button.setEnabled(snapshot.can_restart)
        self.timestamp_input.setEnabled(snapshot.can_seek)
        self.timestamp_button.setEnabled(snapshot.can_seek)
        self.event_index_input.setEnabled(snapshot.can_seek)
        self.event_index_button.setEnabled(snapshot.can_seek)
        self.event_index_input.setMaximum(snapshot.maximum_event_index)

    def _request_timestamp(self) -> None:
        timestamp = self.timestamp_input.dateTime().toPython()
        self.timestamp_requested.emit(timestamp.replace(tzinfo=UTC))


__all__ = ["ReplayPage"]

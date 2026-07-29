from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.replay import (
    ReplaySnapshot,
    ReplaySpeed,
    ReplayState,
    ReplayStatus,
)
from app.recording import (
    RecordingSnapshot,
    RecordingStatus,
)

from app.gui.theme.spacing import Spacing


class ReplayPanel(QWidget):
    """Controls deterministic replay without accessing runtime objects."""

    play_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    step_forward_requested = Signal()
    step_backward_requested = Signal()
    jump_requested = Signal(int)
    speed_requested = Signal(object)
    open_recording_requested = Signal()
    save_recording_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(Spacing.SM)

        summary = QHBoxLayout()
        self.mode = QLabel("LIVE")
        self.mode.setObjectName("statusBadge")
        self.session = QLabel("No replay loaded")
        self.session.setObjectName("muted")
        self.position = QLabel("0 / 0")
        self.position.setObjectName("muted")
        self.timestamp = QLabel("--")
        self.timestamp.setObjectName("muted")
        summary.addWidget(self.mode)
        summary.addWidget(self.session)
        summary.addStretch()
        summary.addWidget(self.position)
        summary.addWidget(self.timestamp)
        root.addLayout(summary)

        recording = QHBoxLayout()
        self.recording_status = QLabel("READY")
        self.recording_status.setObjectName("statusBadge")
        self.recording_duration = QLabel("Duration 0.0s")
        self.recording_duration.setObjectName("muted")
        self.recording_size = QLabel("Size 0 B")
        self.recording_size.setObjectName("muted")
        self.open_button = QPushButton("Open Recording")
        self.save_button = QPushButton("Save Recording")
        recording.addWidget(self.recording_status)
        recording.addWidget(self.recording_duration)
        recording.addWidget(self.recording_size)
        recording.addStretch()
        recording.addWidget(self.open_button)
        recording.addWidget(self.save_button)
        root.addLayout(recording)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Stop")
        self.back_button = QPushButton("Step Back")
        self.step_button = QPushButton("Step")
        self.speed = QComboBox()
        for speed in ReplaySpeed:
            self.speed.addItem(speed.value, speed.value)
        controls.addWidget(self.play_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.back_button)
        controls.addWidget(self.step_button)
        controls.addStretch()
        controls.addWidget(QLabel("Replay Speed"))
        controls.addWidget(self.speed)
        root.addLayout(controls)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        root.addWidget(self.slider)

        self.play_button.clicked.connect(self.play_requested)
        self.pause_button.clicked.connect(self.pause_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.back_button.clicked.connect(
            self.step_backward_requested
        )
        self.step_button.clicked.connect(
            self.step_forward_requested
        )
        self.slider.sliderReleased.connect(self._request_jump)
        self.speed.currentIndexChanged.connect(
            self._request_speed
        )
        self.open_button.clicked.connect(
            self.open_recording_requested
        )
        self.save_button.clicked.connect(
            self.save_recording_requested
        )
        self.render(
            ReplaySnapshot.initial(),
            RecordingSnapshot.initial(),
        )

    def render(
        self,
        snapshot: ReplaySnapshot,
        recording: RecordingSnapshot | None = None,
    ) -> None:
        if not isinstance(snapshot, ReplaySnapshot):
            raise TypeError("snapshot must be a ReplaySnapshot")
        if recording is None:
            recording = RecordingSnapshot.initial()
        if not isinstance(recording, RecordingSnapshot):
            raise TypeError(
                "recording must be a RecordingSnapshot"
            )
        replay_active = snapshot.state is ReplayState.REPLAY
        self.mode.setText(
            (
                "LIVE"
                if not replay_active
                else f"REPLAY · {snapshot.status.value}"
            )
        )
        self.session.setText(
            (
                "No replay loaded"
                if snapshot.session is None
                else snapshot.session.session_id
            )
        )
        self.position.setText(
            f"{snapshot.position.event_index} / "
            f"{snapshot.position.total_events}"
        )
        self.timestamp.setText(
            (
                "--"
                if snapshot.position.timestamp is None
                else snapshot.position.timestamp.astimezone().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
        )
        self.slider.blockSignals(True)
        self.slider.setRange(0, snapshot.position.total_events)
        self.slider.setValue(snapshot.position.event_index)
        self.slider.blockSignals(False)
        speed_index = self.speed.findData(snapshot.speed.value)
        if speed_index >= 0:
            self.speed.blockSignals(True)
            self.speed.setCurrentIndex(speed_index)
            self.speed.blockSignals(False)

        self.play_button.setEnabled(
            replay_active
            and snapshot.status is not ReplayStatus.PLAYING
        )
        self.pause_button.setEnabled(
            replay_active
            and snapshot.status is ReplayStatus.PLAYING
        )
        self.stop_button.setEnabled(replay_active)
        self.back_button.setEnabled(
            replay_active and snapshot.position.event_index > 0
        )
        self.step_button.setEnabled(
            replay_active
            and snapshot.position.event_index
            < snapshot.position.total_events
        )
        self.speed.setEnabled(replay_active)
        self.slider.setEnabled(replay_active)
        self.recording_status.setText(recording.status.value)
        self.recording_duration.setText(
            f"Duration {recording.duration_seconds:f}s"
        )
        self.recording_size.setText(
            f"Size {recording.size_bytes:,} B"
        )
        self.save_button.setEnabled(
            recording.status is RecordingStatus.COMPLETED
        )

    def _request_jump(self) -> None:
        self.jump_requested.emit(self.slider.value())

    def _request_speed(self, index: int) -> None:
        value = self.speed.itemData(index)
        try:
            speed = ReplaySpeed(value)
        except (TypeError, ValueError):
            return
        self.speed_requested.emit(speed)

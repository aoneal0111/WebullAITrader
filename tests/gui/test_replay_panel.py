import os
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.widgets.replay_panel import ReplayPanel
from app.replay import (
    ReplayPosition,
    ReplaySession,
    ReplaySnapshot,
    ReplaySpeed,
    ReplayState,
    ReplayStatus,
)
from app.recording import (
    RecordingSnapshot,
    RecordingState,
    RecordingStatus,
)


APPLICATION = QApplication.instance() or QApplication([])
NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)


def _snapshot() -> ReplaySnapshot:
    return ReplaySnapshot(
        session=ReplaySession(
            session_id="session-1",
            started_at=NOW,
            ended_at=NOW,
            event_count=4,
        ),
        state=ReplayState.REPLAY,
        status=ReplayStatus.PAUSED,
        position=ReplayPosition(
            event_index=2,
            total_events=4,
            sequence_number=2,
            timestamp=NOW,
            progress=Decimal("50"),
        ),
        speed=ReplaySpeed.X5,
    )


def test_replay_panel_renders_snapshot_and_emits_navigation_intents() -> None:
    panel = ReplayPanel()
    events = []
    panel.play_requested.connect(lambda: events.append(("play", None)))
    panel.pause_requested.connect(lambda: events.append(("pause", None)))
    panel.stop_requested.connect(lambda: events.append(("stop", None)))
    panel.step_forward_requested.connect(
        lambda: events.append(("forward", None))
    )
    panel.step_backward_requested.connect(
        lambda: events.append(("backward", None))
    )
    panel.jump_requested.connect(
        lambda value: events.append(("jump", value))
    )
    panel.speed_requested.connect(
        lambda value: events.append(("speed", value))
    )

    panel.render(_snapshot())
    panel.play_button.click()
    panel.stop_button.click()
    panel.step_button.click()
    panel.back_button.click()
    panel.slider.setValue(3)
    panel._request_jump()
    panel.speed.setCurrentIndex(panel.speed.findData(ReplaySpeed.X10))

    assert panel.mode.text() == "REPLAY · PAUSED"
    assert panel.session.text() == "session-1"
    assert panel.position.text() == "2 / 4"
    assert panel.slider.maximum() == 4
    assert ("play", None) in events
    assert ("stop", None) in events
    assert ("forward", None) in events
    assert ("backward", None) in events
    assert ("jump", 3) in events
    assert ("speed", ReplaySpeed.X10) in events
    panel.deleteLater()


def test_replay_panel_renders_recording_status_and_file_intents() -> None:
    panel = ReplayPanel()
    events = []
    panel.open_recording_requested.connect(
        lambda: events.append("open")
    )
    panel.save_recording_requested.connect(
        lambda: events.append("save")
    )
    recording = RecordingSnapshot(
        state=RecordingState.STOPPED,
        status=RecordingStatus.COMPLETED,
        session_id="session-1",
        started_at=NOW,
        ended_at=NOW,
        duration_seconds=Decimal("12.5"),
        event_count=10,
        size_bytes=2048,
        file_path="session.atlas-session.json",
        error=None,
    )

    panel.render(_snapshot(), recording)
    panel.open_button.click()
    panel.save_button.click()

    assert panel.recording_status.text() == "COMPLETED"
    assert panel.recording_duration.text() == "Duration 12.5s"
    assert panel.recording_size.text() == "Size 2,048 B"
    assert events == ["open", "save"]
    panel.deleteLater()

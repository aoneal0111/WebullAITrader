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

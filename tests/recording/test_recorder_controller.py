from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.composition import create_desktop_composition
from app.composition.desktop_runtime_config import (
    DesktopRuntimeConfiguration,
)
from app.operations_core import (
    OperationsBus,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
)
from app.recording import (
    RecordingSerializer,
    RecordingState,
    RecordingStatus,
    SessionRecorder,
)


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)


def test_recorder_preserves_publication_order_and_completes_session() -> None:
    bus = OperationsBus()
    recorder = SessionRecorder(
        bus,
        RecordingSerializer(),
        application_version="0.1.0",
        broker="BROKER_NEUTRAL",
        runtime_mode="PAPER",
        session_id_factory=lambda: "session-1",
    )
    completed = []
    recorder.subscribe_completed(completed.append)
    events = (
        RuntimeStarting(occurred_at=NOW),
        RuntimeStarted(
            active_model="atlas",
            occurred_at=NOW + timedelta(seconds=1),
        ),
        RuntimeStopped(
            cycles_completed=2,
            occurred_at=NOW + timedelta(seconds=2),
        ),
    )
    try:
        for event in events:
            bus.publish(event)

        session = completed[0]
        assert tuple(
            event.sequence_number for event in session.events
        ) == (1, 2, 3)
        assert tuple(
            event.event_type for event in session.events
        ) == (
            "RuntimeStarting",
            "RuntimeStarted",
            "RuntimeStopped",
        )
        snapshot = recorder.snapshot()
        assert snapshot.state is RecordingState.STOPPED
        assert snapshot.status is RecordingStatus.COMPLETED
        assert snapshot.duration_seconds == 2
    finally:
        recorder.close()


def test_recorder_subscribes_once_and_close_is_idempotent() -> None:
    bus = OperationsBus()
    recorder = SessionRecorder(
        bus,
        RecordingSerializer(),
        application_version="0.1.0",
        broker="BROKER_NEUTRAL",
        runtime_mode="PAPER",
    )

    assert bus.subscription_count == 1
    recorder.close()
    recorder.close()
    assert bus.subscription_count == 0


def test_controller_persists_and_opens_recording_for_replay(
    tmp_path: Path,
) -> None:
    composition = create_desktop_composition(
        configuration=DesktopRuntimeConfiguration(
            recording_directory=tmp_path,
        )
    )
    events = (
        RuntimeStarting(occurred_at=NOW),
        RuntimeStarted(
            active_model="atlas",
            occurred_at=NOW + timedelta(seconds=1),
        ),
        RuntimeStopped(
            cycles_completed=1,
            occurred_at=NOW + timedelta(seconds=2),
        ),
    )
    try:
        for event in events:
            composition.bus.publish(event)

        recording = composition.recording_controller.snapshot()
        assert recording.status is RecordingStatus.COMPLETED
        assert recording.size_bytes > 0
        assert recording.file_path is not None
        path = Path(recording.file_path)
        assert path.exists()

        composition.recording_controller.open(path)
        replay = composition.replay_controller.snapshot()
        assert replay.session is not None
        assert replay.session.event_count == 3
        composition.replay_controller.seek(3)
        assert (
            composition.replay_projections
            .state_store
            .snapshot()
            .runtime
            .phase
            .value
            == "STOPPED"
        )
    finally:
        composition.close(timeout_seconds=1.0)


def test_composition_close_persists_an_incomplete_active_session(
    tmp_path: Path,
) -> None:
    composition = create_desktop_composition(
        configuration=DesktopRuntimeConfiguration(
            recording_directory=tmp_path,
        )
    )
    composition.bus.publish(RuntimeStarting(occurred_at=NOW))

    composition.close(timeout_seconds=1.0)

    recordings = tuple(tmp_path.glob("*.atlas-session.json"))
    assert len(recordings) == 1
    assert (
        composition.recording_reader
        .read_archive(recordings[0])
        .events[0]
        .event_id
        is not None
    )

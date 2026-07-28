from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.recording import (
    RecordedEvent,
    RecordedSession,
    RecordingSnapshot,
    RecordingState,
    RecordingStatus,
)


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)


def event(**changes) -> RecordedEvent:
    values = {
        "sequence_number": 1,
        "timestamp": NOW,
        "event_type": "RuntimeStarting",
        "payload": (("environment", "PAPER"),),
        "metadata": (("source", "operations"),),
    }
    values.update(changes)
    return RecordedEvent(**values)


def session(**changes) -> RecordedSession:
    values = {
        "session_id": "session-1",
        "started_at": NOW,
        "ended_at": NOW,
        "strategy_version": "1.0",
        "application_version": "0.1.0",
        "broker": "BROKER_NEUTRAL",
        "runtime_mode": "PAPER",
        "events": (event(),),
    }
    values.update(changes)
    return RecordedSession(**values)


def test_recording_models_are_frozen_and_slotted() -> None:
    model = session()
    snapshot = RecordingSnapshot.initial()

    with pytest.raises(FrozenInstanceError):
        model.session_id = "other"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.runtime = object()  # type: ignore[attr-defined]


def test_initial_snapshot_is_ready_and_empty() -> None:
    assert RecordingSnapshot.initial() == RecordingSnapshot(
        state=RecordingState.IDLE,
        status=RecordingStatus.READY,
        session_id=None,
        started_at=None,
        ended_at=None,
        duration_seconds=Decimal("0"),
        event_count=0,
        size_bytes=0,
        file_path=None,
        error=None,
    )


@pytest.mark.parametrize(
    "factory",
    (
        lambda: event(sequence_number=0),
        lambda: event(timestamp=NOW.replace(tzinfo=None)),
        lambda: event(payload=[("environment", "PAPER")]),
        lambda: event(payload=(("duplicate", 1), ("duplicate", 2))),
        lambda: session(session_id=" "),
        lambda: session(ended_at=NOW - timedelta(seconds=1)),
        lambda: session(events=()),
        lambda: session(events=(event(), event())),
        lambda: RecordingSnapshot(
            state=RecordingState.IDLE,
            status=RecordingStatus.READY,
            session_id="session",
            started_at=None,
            ended_at=None,
            duration_seconds=Decimal("0"),
            event_count=0,
            size_bytes=0,
            file_path=None,
            error=None,
        ),
    ),
)
def test_recording_model_validation(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()

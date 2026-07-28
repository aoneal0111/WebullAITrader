from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState
from app.recording import (
    RecordingSnapshot,
    RecordingState,
    RecordingStatus,
)


NOW = datetime(2026, 7, 28, 20, 0, tzinfo=timezone.utc)


def test_dashboard_carries_immutable_recording_snapshot() -> None:
    recording = RecordingSnapshot(
        state=RecordingState.RECORDING,
        status=RecordingStatus.ACTIVE,
        session_id="session-1",
        started_at=NOW,
        ended_at=None,
        duration_seconds=Decimal("2"),
        event_count=3,
        size_bytes=0,
        file_path=None,
        error=None,
    )

    assert project_dashboard(
        ApplicationState(),
        recording=recording,
    ).recording is recording


def test_dashboard_rejects_wrong_recording_snapshot() -> None:
    with pytest.raises(TypeError, match="RecordingSnapshot"):
        project_dashboard(
            ApplicationState(),
            recording=object(),  # type: ignore[arg-type]
        )

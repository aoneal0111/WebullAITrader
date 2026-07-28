from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState
from app.replay import (
    ReplayPosition,
    ReplaySession,
    ReplaySnapshot,
    ReplaySpeed,
    ReplayState,
    ReplayStatus,
)


NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)


def test_dashboard_carries_immutable_replay_snapshot() -> None:
    replay = ReplaySnapshot(
        session=ReplaySession("session-1", NOW, NOW, 1),
        state=ReplayState.REPLAY,
        status=ReplayStatus.COMPLETED,
        position=ReplayPosition(
            event_index=1,
            total_events=1,
            sequence_number=1,
            timestamp=NOW,
            progress=Decimal("100"),
        ),
        speed=ReplaySpeed.PAUSED,
    )

    assert project_dashboard(
        ApplicationState(),
        replay=replay,
    ).replay is replay


def test_dashboard_rejects_wrong_replay_snapshot() -> None:
    with pytest.raises(TypeError, match="ReplaySnapshot"):
        project_dashboard(
            ApplicationState(),
            replay=object(),  # type: ignore[arg-type]
        )

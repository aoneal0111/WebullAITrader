from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.operations_core import RuntimeStarted, RuntimeStarting
from app.replay import (
    ReplayArchiveEntry,
    ReplayEventArchive,
    ReplayPosition,
    ReplaySession,
    ReplaySnapshot,
    ReplaySpeed,
    ReplayState,
    ReplayStatus,
)


NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)


def test_replay_models_are_frozen_slotted_and_initially_live() -> None:
    snapshot = ReplaySnapshot.initial()

    assert snapshot.state is ReplayState.LIVE
    assert snapshot.status is ReplayStatus.EMPTY
    assert snapshot.speed is ReplaySpeed.PAUSED
    with pytest.raises(FrozenInstanceError):
        snapshot.status = ReplayStatus.READY  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.runtime = object()  # type: ignore[attr-defined]


def test_speed_values_and_multipliers_are_stable() -> None:
    assert tuple(speed.value for speed in ReplaySpeed) == (
        "PAUSED",
        "1X",
        "2X",
        "5X",
        "10X",
        "20X",
    )
    assert ReplaySpeed.X20.multiplier == Decimal("20")


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ReplaySession(
            "",
            NOW,
            NOW,
            1,
        ),
        lambda: ReplaySession(
            "session",
            NOW.replace(tzinfo=None),
            NOW,
            1,
        ),
        lambda: ReplaySession(
            "session",
            NOW,
            NOW - timedelta(seconds=1),
            1,
        ),
        lambda: ReplayPosition(event_index=2, total_events=1),
        lambda: ReplayPosition(
            event_index=1,
            total_events=1,
            progress=Decimal("50"),
        ),
        lambda: ReplayPosition(progress=Decimal("101")),
        lambda: ReplaySnapshot(
            session=None,
            state=ReplayState.REPLAY,
            status=ReplayStatus.READY,
            position=ReplayPosition(),
            speed=ReplaySpeed.PAUSED,
        ),
    ),
)
def test_replay_model_validation(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_archive_preserves_publication_order_not_timestamp_order() -> None:
    first = RuntimeStarting(
        occurred_at=NOW + timedelta(seconds=2),
    )
    second = RuntimeStarted(
        active_model="atlas",
        occurred_at=NOW,
    )

    archive = ReplayEventArchive.from_events((first, second))

    assert archive.events == (first, second)
    assert tuple(
        entry.sequence_number for entry in archive.entries
    ) == (1, 2)
    assert tuple(entry.event_type for entry in archive.entries) == (
        "RuntimeStarting",
        "RuntimeStarted",
    )
    assert archive.session("session-1").started_at == NOW


def test_archive_append_returns_new_immutable_archive() -> None:
    event = RuntimeStarting(occurred_at=NOW)
    empty = ReplayEventArchive()
    populated = empty.append(event)

    assert empty.entries == ()
    assert populated.events == (event,)
    with pytest.raises(FrozenInstanceError):
        populated.entries = ()  # type: ignore[misc]


def test_archive_validates_payload_and_contiguous_sequence() -> None:
    event = RuntimeStarting(occurred_at=NOW)
    entry = ReplayArchiveEntry(
        timestamp=NOW,
        sequence_number=2,
        event_type="RuntimeStarting",
        event_payload=event,
    )

    with pytest.raises(ValueError, match="contiguous"):
        ReplayEventArchive(entries=(entry,))
    with pytest.raises(ValueError, match="event_type"):
        ReplayArchiveEntry(
            timestamp=NOW,
            sequence_number=1,
            event_type="RuntimeStarted",
            event_payload=event,
        )

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from app.read_models.timeline import (
    TimelineCategory,
    TimelineEntry,
    TimelineReadModelSnapshot,
    TimelineSeverity,
    TimelineSnapshot,
)


NOW = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)


def make_entry(**changes) -> TimelineEntry:
    values = {
        "timestamp": NOW,
        "category": TimelineCategory.SYSTEM,
        "severity": TimelineSeverity.INFO,
        "title": "Runtime event",
        "description": "A runtime event occurred.",
        "cycle": 1,
        "symbol": "AAPL",
    }
    values.update(changes)
    return TimelineEntry(**values)


def test_timeline_enums_have_stable_values() -> None:
    assert tuple(category.value for category in TimelineCategory) == (
        "SYSTEM",
        "SCANNER",
        "EVIDENCE",
        "COMMITTEE",
        "DECISION",
        "ORDER",
        "FILL",
        "POSITION",
        "RISK",
        "EXIT",
        "WARNING",
        "ERROR",
    )
    assert tuple(severity.value for severity in TimelineSeverity) == (
        "INFO",
        "SUCCESS",
        "WARNING",
        "ERROR",
    )


def test_timeline_models_are_frozen_and_slotted() -> None:
    entry = make_entry()
    snapshot = TimelineSnapshot(entries=(entry,))

    with pytest.raises(FrozenInstanceError):
        entry.title = "Changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.extra = ()  # type: ignore[attr-defined]


def test_read_model_snapshot_alias_uses_requested_timeline_snapshot() -> None:
    assert TimelineReadModelSnapshot is TimelineSnapshot
    assert TimelineReadModelSnapshot.initial().max_entries == 500


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"timestamp": NOW.replace(tzinfo=None)}, "timezone-aware"),
        ({"title": ""}, "title"),
        ({"description": " padded "}, "description"),
        ({"category": "SYSTEM"}, "TimelineCategory"),
        ({"severity": "INFO"}, "TimelineSeverity"),
        ({"cycle": -1}, "cycle"),
        ({"symbol": ""}, "symbol"),
        ({"symbol": "aapl"}, "uppercase"),
    ),
)
def test_timeline_entry_validation(changes, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        make_entry(**changes)


def test_snapshot_requires_immutable_entries_and_positive_bound() -> None:
    entry = make_entry()

    with pytest.raises(TypeError, match="immutable tuple"):
        TimelineSnapshot(entries=[entry])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        TimelineSnapshot(max_entries=0)
    with pytest.raises(ValueError, match="cannot exceed 500"):
        TimelineSnapshot(max_entries=501)


def test_snapshot_rejects_history_above_bound() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        TimelineSnapshot(
            entries=(make_entry(), make_entry()),
            max_entries=1,
        )

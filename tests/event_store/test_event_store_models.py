from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from app.event_store import (
    EventStoreSnapshot,
    EventStoreStatus,
    IndexedEvent,
    IndexedSession,
    QueryResult,
    QueryStatistics,
)
from app.operations_core import RuntimeStarting


NOW = datetime(2026, 7, 28, 21, 0, tzinfo=timezone.utc)


def indexed_event(**changes):
    event = RuntimeStarting(occurred_at=NOW)
    values = {
        "session_id": "session-1",
        "sequence_number": 1,
        "timestamp": NOW,
        "event_type": "RuntimeStarting",
        "symbols": (),
        "order_ids": (),
        "position_ids": (),
        "decisions": (),
        "lifecycle_phases": (),
        "summary": "RuntimeStarting event",
        "event": event,
    }
    values.update(changes)
    return IndexedEvent(**values)


def test_models_are_frozen_slotted_and_initially_empty() -> None:
    snapshot = EventStoreSnapshot.initial()

    assert snapshot.status is EventStoreStatus.EMPTY
    with pytest.raises(FrozenInstanceError):
        snapshot.status = EventStoreStatus.READY  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.runtime = object()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: indexed_event(sequence_number=0),
        lambda: indexed_event(timestamp=NOW.replace(tzinfo=None)),
        lambda: indexed_event(symbols=["AAPL"]),
        lambda: indexed_event(symbols=("AAPL", "AAPL")),
        lambda: indexed_event(event_type="RuntimeStopped"),
        lambda: IndexedSession(
            "session",
            "path",
            NOW,
            NOW,
            "1",
            "1",
            "broker",
            "PAPER",
            0,
        ),
        lambda: QueryStatistics(0, 1, 2, None, None, ()),
        lambda: QueryResult(
            "query",
            (indexed_event(),),
            QueryStatistics.empty(),
        ),
    ),
)
def test_model_validation(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()

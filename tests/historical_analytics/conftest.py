from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.event_store import (
    EventStoreSnapshot,
    EventStoreStatus,
    EventStoreQueryEngine,
    build_index,
)
from app.operations_core import (
    DecisionsUpdated,
    OperationsDecision,
    TradeLifecycleUpdated,
)
from app.recording import RecordedSession, RecordingSerializer
from app.replay import ReplayEventArchive


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def analytics_event_store_snapshot() -> EventStoreSnapshot:
    events = (
        DecisionsUpdated(
            cycle=1,
            decisions=(
                OperationsDecision(
                    "AAPL", "BUY", 90, Decimal("0.9"),
                    ("approved",), "BUY", Decimal("0"), "v2", NOW,
                ),
                OperationsDecision(
                    "MSFT", "BUY", 80, Decimal("0.8"),
                    ("approved",), "BUY", Decimal("0"), "v1", NOW,
                ),
            ),
            occurred_at=NOW,
        ),
        TradeLifecycleUpdated(
            symbol="AAPL",
            phase="COMMITTEE",
            title="APPROVED",
            description="Committee approved.",
            occurred_at=NOW + timedelta(minutes=1),
        ),
        TradeLifecycleUpdated(
            symbol="AAPL",
            phase="POSITION_OPEN",
            title="Position Open",
            description="Opened.",
            occurred_at=NOW + timedelta(minutes=2),
        ),
        TradeLifecycleUpdated(
            symbol="AAPL",
            phase="POSITION_CLOSE",
            title="Position Close",
            description="Closed.",
            realized_pnl=Decimal("100"),
            occurred_at=NOW + timedelta(minutes=32),
        ),
        TradeLifecycleUpdated(
            symbol="MSFT",
            phase="POSITION_OPEN",
            title="Position Open",
            description="Opened.",
            occurred_at=NOW + timedelta(minutes=3),
        ),
        TradeLifecycleUpdated(
            symbol="MSFT",
            phase="EXIT",
            title="Exit",
            description="Closed.",
            realized_pnl=Decimal("-40"),
            occurred_at=NOW + timedelta(minutes=63),
        ),
    )
    serializer = RecordingSerializer()
    session = RecordedSession(
        "analytics-session",
        NOW,
        events[-1].occurred_at,
        "fallback-v1",
        "0.1.0",
        "BROKER_NEUTRAL",
        "PAPER",
        tuple(
            serializer.record_event(event, sequence)
            for sequence, event in enumerate(events, start=1)
        ),
    )
    index = build_index(
        ((session, ReplayEventArchive.from_events(events), "session.json"),)
    )
    result = EventStoreQueryEngine().query_all(index)
    return EventStoreSnapshot(
        EventStoreStatus.READY,
        index.sessions,
        index.events,
        result,
        result.statistics,
        tuple(key for key, _ in index.symbol_index),
        tuple(key for key, _ in index.event_type_index),
        NOW,
        (),
    )

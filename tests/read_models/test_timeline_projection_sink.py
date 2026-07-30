from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsOrder,
)
from app.paper_trading.models import PaperFill
from app.read_models.timeline import (
    TimelineCategory,
    TimelineEntry,
    TimelineReadModelSnapshot,
    TimelineSeverity,
)
from app.read_models.timeline_projection import TimelineProjection


NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)


def runtime_event(
    sequence: int,
    event_type: str,
    *,
    timestamp: datetime | None = None,
    message: str | None = None,
    source: str = "paper-runtime",
    symbol: str | None = None,
    order: OperationsOrder | None = None,
    fill: PaperFill | None = None,
) -> PaperRuntimeEvent:
    occurred_at = timestamp or NOW + timedelta(minutes=sequence)
    return PaperRuntimeEvent(
        sequence=sequence,
        timestamp=occurred_at,
        event_type=event_type,
        message=message or event_type.replace("_", " ").title(),
        cycle=max(sequence, 0),
        symbol=symbol,
        order=order,
        fill=fill,
        source=source,
    )


def order(
    *,
    status: str,
    timestamp: datetime,
) -> OperationsOrder:
    return OperationsOrder(
        order_id="order-1",
        symbol="AAPL",
        side="BUY",
        quantity="10",
        status=status,
        updated_at=timestamp,
    )


def fill(timestamp: datetime) -> PaperFill:
    return PaperFill(
        request_id="order-1",
        symbol="AAPL",
        side="BUY",
        quantity=Decimal("10"),
        fill_price=Decimal("100"),
        notional=Decimal("1000"),
        realized_pnl=Decimal("0"),
        timestamp=timestamp,
    )


def test_timeline_entry_and_snapshot_are_immutable() -> None:
    entry = TimelineEntry(
        timestamp=NOW,
        category=TimelineCategory.RUNTIME,
        severity=TimelineSeverity.SUCCESS,
        source="paper-runtime",
        title="Runtime started",
        description="Paper runtime started.",
    )
    snapshot = TimelineReadModelSnapshot(entries=(entry,))

    with pytest.raises(AttributeError):
        entry.title = "changed"  # type: ignore[misc]

    assert snapshot.entries == (entry,)


def test_projection_stores_entries_newest_first_by_timestamp() -> None:
    projection = TimelineProjection(OperationsBus())
    latest = NOW + timedelta(minutes=10)
    middle = NOW + timedelta(minutes=5)

    projection(runtime_event(1, "STARTED", timestamp=latest))
    projection(runtime_event(2, "BROKER_CONNECTED", timestamp=NOW))
    projection(runtime_event(3, "MODEL_LOADED", timestamp=middle))

    assert tuple(
        entry.timestamp
        for entry in projection.snapshot.entries
    ) == (latest, middle, NOW)


def test_equal_timestamps_use_sequence_as_deterministic_tiebreaker() -> None:
    projection = TimelineProjection(OperationsBus())

    projection(runtime_event(1, "STARTED", timestamp=NOW))
    projection(runtime_event(2, "STOPPED", timestamp=NOW))

    assert tuple(
        entry.title
        for entry in projection.snapshot.entries
    ) == ("Runtime stopped", "Runtime started")


def test_projection_bounds_history_to_configured_maximum() -> None:
    projection = TimelineProjection(
        OperationsBus(),
        maximum_entries=2,
    )

    projection(runtime_event(1, "STARTED"))
    projection(runtime_event(2, "BROKER_CONNECTED"))
    projection(runtime_event(3, "MARKET_DATA_CONNECTED"))

    assert tuple(
        entry.title
        for entry in projection.snapshot.entries
    ) == (
        "Market data connected",
        "Broker connected",
    )


def test_evicted_old_event_does_not_republish_or_expand_history() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = TimelineProjection(bus, maximum_entries=2)
    oldest = runtime_event(1, "STARTED")

    projection(oldest)
    projection(runtime_event(2, "BROKER_CONNECTED"))
    projection(runtime_event(3, "MARKET_DATA_CONNECTED"))
    revision = store.snapshot().revision
    projection(oldest)

    assert len(projection.snapshot.entries) == 2
    assert store.snapshot().revision == revision


def test_projection_classifies_multiple_significant_categories() -> None:
    projection = TimelineProjection(OperationsBus())
    accepted_at = NOW + timedelta(minutes=5)
    filled_at = NOW + timedelta(minutes=6)

    events = (
        runtime_event(1, "STARTED"),
        runtime_event(2, "BROKER_CONNECTED"),
        runtime_event(3, "MARKET_DATA_DISCONNECTED"),
        runtime_event(4, "MODEL_LOADED"),
        runtime_event(
            5,
            "DECISION_PROCESSED",
            timestamp=accepted_at,
            symbol="AAPL",
            order=order(status="ACCEPTED", timestamp=accepted_at),
        ),
        runtime_event(
            6,
            "DECISION_PROCESSED",
            timestamp=filled_at,
            symbol="AAPL",
            order=order(status="FILLED", timestamp=filled_at),
            fill=fill(filled_at),
        ),
        runtime_event(7, "FAILED", message="Synthetic failure."),
    )
    for event in events:
        projection(event)

    by_title = {
        entry.title: entry
        for entry in projection.snapshot.entries
    }
    assert by_title["Runtime started"].category is TimelineCategory.RUNTIME
    assert by_title["Broker connected"].category is TimelineCategory.BROKER
    assert (
        by_title["Market data disconnected"].category
        is TimelineCategory.MARKET_DATA
    )
    assert by_title["AI model loaded"].category is TimelineCategory.AI
    assert by_title["Order accepted"].category is TimelineCategory.ORDER
    assert by_title["Order filled"].category is TimelineCategory.EXECUTION
    assert by_title["Runtime failed"].severity is TimelineSeverity.ERROR
    assert (
        by_title["Market data disconnected"].severity
        is TimelineSeverity.WARNING
    )
    assert by_title["Order filled"].related_symbol == "AAPL"
    assert by_title["Order filled"].related_order_id == "order-1"


def test_duplicate_source_sequence_is_ignored() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = TimelineProjection(bus)
    original = runtime_event(1, "STARTED")

    projection(original)
    revision = store.snapshot().revision
    projection(
        runtime_event(
            1,
            "FAILED",
            message="Duplicate sequence.",
        )
    )

    assert len(projection.snapshot.entries) == 1
    assert projection.snapshot.entries[0].title == "Runtime started"
    assert store.snapshot().revision == revision


@pytest.mark.parametrize(
    ("status", "expected_title", "expected_severity"),
    (
        ("SUBMITTED", "Order submitted", TimelineSeverity.INFO),
        ("ACCEPTED", "Order accepted", TimelineSeverity.SUCCESS),
        ("CANCELLED", "Order cancelled", TimelineSeverity.WARNING),
        ("REJECTED", "Order rejected", TimelineSeverity.WARNING),
    ),
)
def test_projection_captures_significant_order_statuses(
    status: str,
    expected_title: str,
    expected_severity: TimelineSeverity,
) -> None:
    projection = TimelineProjection(OperationsBus())
    occurred_at = NOW + timedelta(minutes=1)

    projection(
        runtime_event(
            1,
            "ORDER_UPDATED",
            timestamp=occurred_at,
            symbol="AAPL",
            order=order(status=status, timestamp=occurred_at),
        )
    )

    entry = projection.snapshot.entries[0]
    assert entry.title == expected_title
    assert entry.category is TimelineCategory.ORDER
    assert entry.severity is expected_severity


def test_projection_captures_explicit_errors_and_warnings() -> None:
    projection = TimelineProjection(OperationsBus())

    projection(runtime_event(1, "BROKER_ERROR"))
    projection(runtime_event(2, "MARKET_DATA_WARNING"))

    by_title = {
        entry.title: entry
        for entry in projection.snapshot.entries
    }
    assert by_title["Broker Error"].severity is TimelineSeverity.ERROR
    assert (
        by_title["Market Data Warning"].severity
        is TimelineSeverity.WARNING
    )


def test_same_sequence_from_different_sources_is_distinct() -> None:
    projection = TimelineProjection(OperationsBus())

    projection(runtime_event(1, "BROKER_CONNECTED", source="broker"))
    projection(
        runtime_event(
            1,
            "MARKET_DATA_CONNECTED",
            source="market-data",
        )
    )

    assert len(projection.snapshot.entries) == 2


def test_noisy_cycle_and_hold_events_are_not_projected() -> None:
    projection = TimelineProjection(OperationsBus())

    projection(runtime_event(1, "CYCLE_COMPLETED"))
    projection(runtime_event(2, "DECISION_PROCESSED"))

    assert projection.snapshot == TimelineReadModelSnapshot.initial()


def test_projection_updates_application_timeline_projection() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projection = TimelineProjection(bus)

    projection(runtime_event(1, "BROKER_CONNECTED"))

    state = store.snapshot()
    assert state.timeline_projection == projection.snapshot
    assert state.timeline_projection.entries[0].source == "paper-runtime"
    assert state.timeline == ()


def test_projection_requires_positive_history_limit() -> None:
    with pytest.raises(
        ValueError,
        match="maximum_entries must be a positive integer",
    ):
        TimelineProjection(OperationsBus(), maximum_entries=0)

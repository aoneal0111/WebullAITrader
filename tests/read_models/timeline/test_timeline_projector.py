from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsEvent,
    OrdersUpdated,
    PaperOrderLifecycleUpdated,
    PaperRuntimeSnapshot,
    PaperRuntimeUpdated,
    PositionsUpdated,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
)
from app.read_models.timeline import (
    TimelineCategory,
    TimelineProjector,
    TimelineSeverity,
)


NOW = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)


def paper_snapshot(cycle: int = 2) -> PaperRuntimeSnapshot:
    return PaperRuntimeSnapshot(
        cycle=cycle,
        timestamp=NOW,
        session_id="paper-1",
        symbols=("AAPL",),
        decisions_processed=1,
        orders_attempted=0,
        orders_filled=0,
        orders_rejected=0,
        orders_not_filled=0,
        decisions_skipped=1,
        winning_fills=0,
        losing_fills=0,
        breakeven_fills=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        current_equity=Decimal("10000"),
        peak_equity=Decimal("10000"),
        current_drawdown=Decimal("0"),
        win_rate=Decimal("0"),
        total_return=Decimal("0"),
        maximum_drawdown=Decimal("0"),
    )


def test_projector_creates_one_entry_for_each_supported_event() -> None:
    bus = OperationsBus()
    projector = TimelineProjector(bus)
    events = (
        RuntimeStarting(occurred_at=NOW),
        RuntimeStarted(occurred_at=NOW, active_model="atlas"),
        RuntimeStopping(occurred_at=NOW, reason="Stopping."),
        RuntimeStopped(occurred_at=NOW, cycles_completed=2),
        RuntimeFailed(occurred_at=NOW, error_message="failure"),
        PaperRuntimeUpdated(
            occurred_at=NOW,
            snapshot=paper_snapshot(),
        ),
        DecisionsUpdated(
            occurred_at=NOW,
            cycle=2,
            decisions=(),
        ),
        OrdersUpdated(occurred_at=NOW, orders=()),
        PositionsUpdated(occurred_at=NOW, positions=()),
        RuntimeCycleCompleted(occurred_at=NOW, cycle_count=2),
    )
    try:
        for event in events:
            bus.publish(event)

        entries = projector.snapshot().entries
        assert len(entries) == len(events)
        assert entries[0].title == "Runtime cycle completed"
        assert entries[-1].title == "Runtime starting"
        assert entries[0].cycle == 2
        assert entries[4].category is TimelineCategory.SYSTEM
        assert entries[5].category is TimelineCategory.ERROR
        assert entries[5].severity is TimelineSeverity.ERROR
    finally:
        projector.close()


def test_history_is_newest_first_by_publication_order() -> None:
    bus = OperationsBus()
    projector = TimelineProjector(bus)
    try:
        bus.publish(
            OperationsEvent(
                occurred_at=NOW + timedelta(seconds=2),
                source="first",
            )
        )
        bus.publish(
            OperationsEvent(
                occurred_at=NOW,
                source="second",
            )
        )

        entries = projector.snapshot().entries
        assert "second" in entries[0].description
        assert "first" in entries[1].description
    finally:
        projector.close()


def test_bounded_history_discards_oldest_entries() -> None:
    bus = OperationsBus()
    projector = TimelineProjector(bus, max_entries=3)
    try:
        for index in range(5):
            bus.publish(
                OperationsEvent(
                    occurred_at=NOW + timedelta(seconds=index),
                    source=f"event-{index}",
                )
            )

        snapshot = projector.snapshot()
        assert snapshot.max_entries == 3
        assert len(snapshot.entries) == 3
        assert tuple(
            entry.description.split(" from ")[1].removesuffix(".")
            for entry in snapshot.entries
        ) == ("event-4", "event-3", "event-2")
    finally:
        projector.close()


def test_fill_lifecycle_event_is_classified_as_successful_fill() -> None:
    bus = OperationsBus()
    projector = TimelineProjector(bus)
    try:
        bus.publish(
            PaperOrderLifecycleUpdated(
                occurred_at=NOW,
                order_id="order-1",
                previous_status="ACCEPTED",
                current_status="FILLED",
                filled_quantity=Decimal("1"),
                remaining_quantity=Decimal("0"),
                fill_price=Decimal("100"),
            )
        )

        entry = projector.snapshot().entries[0]
        assert entry.category is TimelineCategory.FILL
        assert entry.severity is TimelineSeverity.SUCCESS
        assert "order-1" in entry.description
    finally:
        projector.close()


def test_invalid_history_bound_is_rejected_before_subscription() -> None:
    bus = OperationsBus()

    with pytest.raises(ValueError, match="positive"):
        TimelineProjector(bus, max_entries=0)

    assert bus.subscription_count == 0


def test_close_unsubscribes_and_is_idempotent() -> None:
    bus = OperationsBus()
    projector = TimelineProjector(bus)

    assert bus.subscription_count == 1
    projector.close()
    projector.close()

    assert bus.subscription_count == 0

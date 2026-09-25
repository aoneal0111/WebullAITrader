from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

from app.strategies.warrior_momentum.completed_bar_handoff import (
    BoundedCompletedBarResearchHandoff,
    CompletedBarResearchWork,
    CompletedBarSubmitResult,
)
from app.strategies.warrior_momentum.models import MinuteBar


T0 = datetime(2026, 9, 24, 14, 30, tzinfo=UTC)


def work(minute: int) -> CompletedBarResearchWork:
    value = Decimal(str(10 + minute / 100))
    return CompletedBarResearchWork(
        symbol="XYZ",
        session="REGULAR",
        revision=minute + 1,
        bar=MinuteBar(
            "XYZ", T0 + timedelta(minutes=minute),
            value, value, value, value, Decimal("100"),
        ),
        shadow_evaluation_ids=("evaluation-1",),
    )


def test_handoff_preserves_order_suppresses_duplicates_and_bounds_queue() -> None:
    entered = Event()
    release = Event()
    observed: list[datetime] = []

    def handler(value: CompletedBarResearchWork) -> None:
        entered.set()
        release.wait(2.0)
        observed.append(value.bar.timestamp)

    handoff = BoundedCompletedBarResearchHandoff(handler, capacity=2)
    assert handoff.submit(work(0)) is CompletedBarSubmitResult.ACCEPTED
    assert entered.wait(1.0)
    assert handoff.submit(work(1)) is CompletedBarSubmitResult.ACCEPTED
    assert handoff.submit(work(1)) is CompletedBarSubmitResult.DUPLICATE
    assert handoff.submit(work(2)) is CompletedBarSubmitResult.ACCEPTED
    assert handoff.submit(work(3)) is CompletedBarSubmitResult.REJECTED

    release.set()
    assert handoff.wait_idle(2.0)
    assert observed == [work(index).bar.timestamp for index in range(3)]
    metrics = handoff.metrics()
    assert metrics.accepted == 3
    assert metrics.completed == 3
    assert metrics.duplicate_suppressed == 1
    assert metrics.rejected == 1
    assert metrics.high_water == 2
    assert handoff.stop(drain=True, timeout_seconds=1.0)


def test_worker_contains_handler_failure_and_drains_on_shutdown() -> None:
    observed: list[datetime] = []

    def handler(value: CompletedBarResearchWork) -> None:
        if value.revision == 1:
            raise RuntimeError("research failure")
        observed.append(value.bar.timestamp)

    handoff = BoundedCompletedBarResearchHandoff(handler, capacity=4)
    assert handoff.submit(work(0)) is CompletedBarSubmitResult.ACCEPTED
    assert handoff.submit(work(1)) is CompletedBarSubmitResult.ACCEPTED
    assert handoff.stop(drain=True, timeout_seconds=2.0)
    assert observed == [work(1).bar.timestamp]
    metrics = handoff.metrics()
    assert metrics.completed == 2
    assert metrics.failures == 1
    assert metrics.depth == 0

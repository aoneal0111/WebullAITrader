from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.operations_core import (
    OperationsBus,
    OperationsEvent,
    RuntimeCycleCompleted,
)
from app.replay import (
    ReplayClock,
    ReplayEngine,
    ReplayEventArchive,
    ReplaySpeed,
    ReplayStatus,
)


NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)


def _events() -> tuple[OperationsEvent, ...]:
    return tuple(
        RuntimeCycleCompleted(
            cycle_count=index + 1,
            occurred_at=NOW + timedelta(seconds=index),
        )
        for index in range(3)
    )


def _engine():
    received: list[OperationsEvent] = []

    def fresh_bus(prefix: tuple[OperationsEvent, ...]) -> OperationsBus:
        received.clear()
        bus = OperationsBus()
        bus.subscribe(OperationsEvent, received.append)
        for event in prefix:
            bus.publish(event)
        return bus

    bus = fresh_bus(())
    clock = ReplayClock()
    engine = ReplayEngine(bus, clock, reset_sink=fresh_bus)
    engine.load(ReplayEventArchive.from_events(_events()))
    return engine, clock, received


def test_step_seek_and_backward_rebuild_preserve_exact_prefix() -> None:
    engine, clock, received = _engine()
    expected = engine.archive.events

    assert engine.step_forward() == expected[0]
    assert engine.step_forward() == expected[1]
    assert received == list(expected[:2])

    assert engine.step_backward() == expected[1]
    assert received == list(expected[:1])
    assert engine.event_index == 1

    engine.seek(3)
    assert received == list(expected)
    assert engine.status is ReplayStatus.COMPLETED

    engine.stop()
    assert received == []
    assert engine.event_index == 0
    assert clock.elapsed == Decimal("0")


def test_logical_advance_pause_resume_and_speed_are_deterministic() -> None:
    engine, clock, received = _engine()
    expected = engine.archive.events
    engine.set_speed(ReplaySpeed.X2)
    engine.play()

    assert engine.advance(Decimal("0")) == 1
    assert received == [expected[0]]
    assert engine.advance(Decimal("0.5")) == 1
    assert received == list(expected[:2])

    engine.pause()
    assert engine.advance(Decimal("100")) == 0
    assert received == list(expected[:2])

    engine.resume()
    assert engine.advance(Decimal("0.5")) == 1
    assert received == list(expected)
    assert engine.status is ReplayStatus.COMPLETED
    assert clock.speed is ReplaySpeed.PAUSED


def test_close_is_idempotent() -> None:
    engine, _, _ = _engine()

    engine.close()
    engine.close()

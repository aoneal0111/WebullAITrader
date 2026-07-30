from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.composition.runtime_event_sink import CompositeRuntimeEventSink
from app.operations.runtime import PaperRuntimeEvent


def runtime_event() -> PaperRuntimeEvent:
    return PaperRuntimeEvent(
        sequence=1,
        timestamp=datetime(2026, 7, 29, 12, 0, tzinfo=UTC),
        event_type="STARTED",
        message="Paper runtime started.",
        cycle=0,
    )


def test_forwards_event_to_sinks_in_registration_order() -> None:
    calls: list[tuple[str, PaperRuntimeEvent]] = []

    def first(event: PaperRuntimeEvent) -> None:
        calls.append(("first", event))

    def second(event: PaperRuntimeEvent) -> None:
        calls.append(("second", event))

    sink = CompositeRuntimeEventSink((first, second))
    event = runtime_event()

    sink(event)

    assert calls == [
        ("first", event),
        ("second", event),
    ]
    assert sink.sinks == (first, second)


def test_ignores_none_sinks() -> None:
    received: list[PaperRuntimeEvent] = []
    consumer = received.append
    sink = CompositeRuntimeEventSink((None, consumer, None))
    event = runtime_event()

    sink(event)

    assert received == [event]
    assert sink.sinks == (consumer,)


def test_allows_empty_sink_collection() -> None:
    sink = CompositeRuntimeEventSink(())

    sink(runtime_event())

    assert sink.sinks == ()


def test_rejects_non_callable_sink() -> None:
    with pytest.raises(
        TypeError,
        match="every runtime event sink must be callable",
    ):
        CompositeRuntimeEventSink((object(),))

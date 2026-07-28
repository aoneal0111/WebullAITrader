from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtesting.market_feed import InMemoryHistoricalMarketFeed
from app.backtesting.models import PlaybackStatus
from app.backtesting.playback_engine import PlaybackEngine
from app.market_data import MarketEvent, MarketEventType, TradePayload

NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def market_event(sequence: int, offset_seconds: int) -> MarketEvent:
    return MarketEvent(
        sequence,
        NOW + timedelta(seconds=offset_seconds),
        "AAPL",
        "historical",
        MarketEventType.TRADE,
        TradePayload(Decimal("100"), Decimal("10"), f"trade-{sequence}"),
    )


def test_feed_orders_by_timestamp_with_stable_equal_timestamp_tie_breaker() -> None:
    events = (
        market_event(3, 2),
        market_event(2, 0),
        market_event(1, 0),
    )
    feed = InMemoryHistoricalMarketFeed(events)
    assert tuple(event.sequence for event in feed.events()) == (2, 1, 3)
    assert feed.start_time == NOW
    assert feed.end_time == NOW + timedelta(seconds=2)
    assert feed.event_count == 3


def test_step_seek_restart_speed_stop_and_close(historical_events) -> None:
    emitted = []
    engine = PlaybackEngine(emitted.append)
    engine.load(InMemoryHistoricalMarketFeed(historical_events))
    assert engine.step().status is PlaybackStatus.PAUSED
    assert emitted == [historical_events[0]]
    assert engine.seek(2).position == 2
    assert engine.step().status is PlaybackStatus.COMPLETED
    assert engine.restart().position == 0
    assert engine.set_speed(Decimal("5")).speed == Decimal("5")
    assert engine.stop().status is PlaybackStatus.STOPPED
    engine.close()
    engine.close()
    assert engine.snapshot().status is PlaybackStatus.CLOSED
    with pytest.raises(RuntimeError):
        engine.restart()


def test_pause_and_resume_are_deterministic(historical_events) -> None:
    emitted = []
    engine = None

    def sink(event):
        emitted.append(event)
        if len(emitted) == 1:
            engine.pause()

    engine = PlaybackEngine(sink)
    engine.load(InMemoryHistoricalMarketFeed(historical_events))
    assert engine.start().status is PlaybackStatus.PAUSED
    assert len(emitted) == 1
    assert engine.resume().status is PlaybackStatus.COMPLETED
    assert emitted == list(historical_events)


def test_time_range_completion_and_invalid_transitions(historical_events) -> None:
    emitted = []
    engine = PlaybackEngine(emitted.append)
    engine.load(
        InMemoryHistoricalMarketFeed(historical_events),
        start_time=NOW + timedelta(seconds=1),
        end_time=NOW + timedelta(seconds=2),
    )
    assert engine.start().status is PlaybackStatus.COMPLETED
    assert tuple(event.sequence for event in emitted) == (2, 3)
    with pytest.raises(RuntimeError):
        engine.pause()
    with pytest.raises(ValueError):
        engine.set_speed(Decimal("0"))


def test_sink_failure_produces_error_snapshot(historical_events) -> None:
    def fail(event):
        raise RuntimeError(f"failed {event.sequence}")

    engine = PlaybackEngine(fail)
    engine.load(InMemoryHistoricalMarketFeed(historical_events))
    snapshot = engine.start()
    assert snapshot.status is PlaybackStatus.ERROR
    assert snapshot.position == 0
    assert snapshot.error == "failed 1"

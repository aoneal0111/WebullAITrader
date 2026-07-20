from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.market_data.clock import heartbeat_is_stale, measure_clock
from app.market_data.corporate_actions import corporate_actions
from app.market_data.events import append_event
from app.market_data.models import (
    BookLevel, ClockSyncPayload, CorporateActionPayload, CorporateActionType, HeartbeatPayload,
    MarketEvent, MarketEventLog, MarketEventType, MarketSession, MarketStatusPayload,
    OrderBookDeltaPayload, OrderBookSnapshotPayload, QuotePayload, ResumePayload,
    SessionChangePayload, SymbolMetadataPayload, TradePayload, TradingHaltPayload,
)
from app.market_data.recorder import event_log_from_json, event_log_to_json
from app.market_data.replay import ReplayConfig, ReplayTiming, create_replay, next_event, pause, replay_all, resume, seek
from app.market_data.report import market_data_to_json, market_data_to_text
from app.market_data.sessions import latest_recorded_session
from app.market_data.stream import collect_available

D = Decimal
T0 = datetime(2026, 7, 18, 13, tzinfo=UTC)


def event(sequence, kind, payload, *, timestamp=None, symbol="XYZ", source="feed"):
    if kind in (MarketEventType.HEARTBEAT, MarketEventType.CLOCK_SYNC): symbol = None
    return MarketEvent(sequence, timestamp or T0 + timedelta(seconds=sequence), symbol, source, kind, payload)


PAYLOADS = (
    (MarketEventType.QUOTE, QuotePayload(D("10"), D("11"), D("2"), D("3"))),
    (MarketEventType.TRADE, TradePayload(D("10.5"), D("1"), "trade-1")),
    (MarketEventType.BOOK_SNAPSHOT, OrderBookSnapshotPayload((BookLevel(D("10"), D("2")),), (BookLevel(D("11"), D("3")),))),
    (MarketEventType.BOOK_DELTA, OrderBookDeltaPayload("BID", D("10"), D("4"), "UPDATE")),
    (MarketEventType.MARKET_STATUS, MarketStatusPayload("OPEN")),
    (MarketEventType.TRADING_HALT, TradingHaltPayload("NEWS")),
    (MarketEventType.RESUME, ResumePayload("RESUMED")),
    (MarketEventType.SYMBOL_METADATA, SymbolMetadataPayload("NASDAQ", "USD", D("0.01"))),
    (MarketEventType.CORPORATE_ACTION, CorporateActionPayload(CorporateActionType.SPLIT, T0, ratio=D("2"))),
    (MarketEventType.SESSION_CHANGE, SessionChangePayload(MarketSession.REGULAR)),
    (MarketEventType.HEARTBEAT, HeartbeatPayload("connection-1")),
    (MarketEventType.CLOCK_SYNC, ClockSyncPayload(T0, T0 + timedelta(microseconds=5))),
)


@pytest.mark.parametrize(("kind", "payload"), PAYLOADS)
def test_every_event_type_round_trips(kind, payload):
    original = append_event(MarketEventLog(), event(1, kind, payload))
    assert event_log_from_json(event_log_to_json(original)) == original


@pytest.mark.parametrize("bad", (
    MarketEvent(1, datetime(2026, 1, 1), "XYZ", "feed", MarketEventType.QUOTE, QuotePayload(D("1"), D("2"), D("0"), D("0"))),
    event(1, MarketEventType.QUOTE, QuotePayload(D("2"), D("1"), D("0"), D("0"))),
    event(1, MarketEventType.TRADE, TradePayload(D("NaN"), D("1"), "x")),
    event(1, MarketEventType.TRADE, TradePayload(D("1"), D("-1"), "x")),
    MarketEvent(1, T0, "bad symbol!", "feed", MarketEventType.TRADE, TradePayload(D("1"), D("1"), "x")),
    event(1, MarketEventType.QUOTE, TradePayload(D("1"), D("1"), "x")),
))
def test_validation_failures(bad):
    with pytest.raises(ValueError): append_event(MarketEventLog(), bad)


def test_duplicate_sequence_and_timestamp_order_are_rejected():
    log = append_event(MarketEventLog(), event(1, *PAYLOADS[0]))
    with pytest.raises(ValueError, match="duplicate"):
        append_event(log, event(1, *PAYLOADS[1]))
    with pytest.raises(ValueError, match="sequence"):
        append_event(log, event(0, *PAYLOADS[1]))
    with pytest.raises(ValueError, match="timestamp"):
        append_event(log, event(2, *PAYLOADS[1], timestamp=T0))


def populated_log():
    log = MarketEventLog()
    for index, (kind, payload) in enumerate(PAYLOADS[:5], 1): log = append_event(log, event(index, kind, payload))
    return log


def test_recorder_is_canonical_versioned_and_backward_compatible():
    log = populated_log()
    assert event_log_to_json(log) == event_log_to_json(log)
    legacy = json.loads(event_log_to_json(log)); legacy.pop("schema_version")
    assert event_log_from_json(json.dumps(legacy)) == log
    with pytest.raises(ValueError, match="schema"):
        event_log_from_json('{"schema_version":99,"events":[]}')


def test_replay_original_accelerated_and_fixed_step_are_deterministic():
    log = populated_log()
    original = replay_all(create_replay(log, ReplayConfig(ReplayTiming.ORIGINAL)))[1]
    accelerated = replay_all(create_replay(log, ReplayConfig(ReplayTiming.ACCELERATED, D("2"))))[1]
    fixed = replay_all(create_replay(log, ReplayConfig(ReplayTiming.FIXED_STEP, fixed_step_microseconds=7)))[1]
    assert tuple(item.delay_microseconds for item in original) == (0, 1_000_000, 1_000_000, 1_000_000, 1_000_000)
    assert tuple(item.delay_microseconds for item in accelerated)[1:] == (500_000,) * 4
    assert tuple(item.delay_microseconds for item in fixed)[1:] == (7,) * 4
    assert replay_all(create_replay(log, ReplayConfig(ReplayTiming.ORIGINAL)))[1] == original


def test_pause_resume_seek_and_filtering():
    log = populated_log()
    config = ReplayConfig(ReplayTiming.ORIGINAL, symbols=("XYZ",),
                          start_timestamp=T0 + timedelta(seconds=2), end_timestamp=T0 + timedelta(seconds=4))
    state = create_replay(log, config)
    assert tuple(item.sequence for item in state.events) == (2, 3, 4)
    assert next_event(pause(state))[1] is None
    resumed, emission = next_event(resume(pause(state))); assert emission.event.sequence == 2
    assert next_event(seek(resumed, sequence=4))[1].event.sequence == 4
    assert next_event(seek(resumed, timestamp=T0 + timedelta(seconds=3)))[1].event.sequence == 3


def test_stream_transport_is_fully_mockable():
    class Transport:
        def __init__(self): self.items = [event(1, *PAYLOADS[0]), event(2, *PAYLOADS[1])]
        def read_event(self): return self.items.pop(0) if self.items else None
    assert len(collect_available(Transport(), MarketEventLog()).events) == 2


def test_session_changes_and_corporate_actions_are_explicit():
    session = event(1, MarketEventType.SESSION_CHANGE, SessionChangePayload(MarketSession.HALTED))
    action = event(2, MarketEventType.CORPORATE_ACTION,
                   CorporateActionPayload(CorporateActionType.DIVIDEND, T0, cash_amount=D("0.25")))
    assert latest_recorded_session((session, action), "XYZ") is MarketSession.HALTED
    assert corporate_actions((session, action), "XYZ") == (action,)


@pytest.mark.parametrize(("kind", "kwargs"), (
    (CorporateActionType.SPLIT, {"ratio": D("2")}),
    (CorporateActionType.REVERSE_SPLIT, {"ratio": D("0.1")}),
    (CorporateActionType.DIVIDEND, {"cash_amount": D("0.25")}),
    (CorporateActionType.SYMBOL_CHANGE, {"new_symbol": "XYZ2"}),
    (CorporateActionType.MERGER, {}),
    (CorporateActionType.DELISTING, {}),
))
def test_every_corporate_action_type(kind, kwargs):
    action = event(1, MarketEventType.CORPORATE_ACTION, CorporateActionPayload(kind, T0, **kwargs))
    assert append_event(MarketEventLog(), action).events == (action,)


def test_clock_and_heartbeat_do_not_adjust_timestamps():
    payload = ClockSyncPayload(T0, T0 + timedelta(microseconds=10))
    measurement = measure_clock(payload, T0 + timedelta(microseconds=25))
    assert (measurement.latency_microseconds, measurement.clock_skew_microseconds) == (25, 10)
    heartbeat = event(1, MarketEventType.HEARTBEAT, HeartbeatPayload("c"), timestamp=T0)
    assert not heartbeat_is_stale((heartbeat,), T0 + timedelta(microseconds=5), 5)
    assert heartbeat_is_stale((heartbeat,), T0 + timedelta(microseconds=6), 5)
    assert heartbeat.timestamp == T0


def test_reports_are_stable_and_decimal_exact():
    log = populated_log()
    assert market_data_to_json(log) == market_data_to_json(log)
    assert market_data_to_text(log) == market_data_to_text(log)
    assert '"bid":"10"' in market_data_to_json(log)


def test_import_boundary_has_no_execution_or_upstream_dependencies():
    package = Path(__file__).parents[1] / "app" / "market_data"
    content = "\n".join(path.read_text(encoding="utf-8").lower() for path in package.glob("*.py"))
    forbidden = ("app.live_execution", "app.ai", "app.indicators", "app.strategy", "app.risk",
                 "app.analytics", "app.monte_carlo", "app.stress_testing", "app.compliance",
                 "requests", "httpx", "mcp")
    assert not any(f"from {item}" in content or f"import {item}" in content for item in forbidden)

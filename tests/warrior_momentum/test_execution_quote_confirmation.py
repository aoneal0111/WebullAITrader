from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from app.strategies.warrior_momentum.execution_quote import (
    ExecutionQuoteSnapshot, parse_execution_quote,
)
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.models import SetupState
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime
from tests.warrior_momentum.test_forward_capture import T0, account, point, scanner


NOW = T0 + timedelta(minutes=20)


def snapshot(*, age: str = "1", bid: str = "10.00", ask: str = "10.0050",
             last: str = "10.0050", confirmed_at=NOW) -> ExecutionQuoteSnapshot:
    timestamp = confirmed_at - timedelta(seconds=float(age))
    return ExecutionQuoteSnapshot(
        "XYZ", D(last), D(bid), D(ask), timestamp, timestamp, timestamp, confirmed_at,
    )


def evaluate(tmp_path, source, *, value=None, permitted=lambda: True, runtime=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = ForwardCaptureStore(tmp_path / "execution.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        execution_quote_source=source,
        execution_permitted=permitted,
    )
    if runtime is not None:
        service.runtime = runtime
    try:
        return service.observe(
            value or point(
                evaluation_timestamp=NOW,
                quote_freshness_seconds=D("30"),
                last_price_freshness_seconds=D("30"),
            ),
            account=account(),
        )
    finally:
        writer.close()


def test_fresh_authoritative_refresh_reruns_gates_then_continues_once(tmp_path) -> None:
    calls = []
    candidate, signal = evaluate(tmp_path, lambda symbol: calls.append(symbol) or snapshot())
    assert calls == ["XYZ"]
    assert candidate.status.value == "ENTRY_READY"
    assert signal is not None and signal.reference_price == D("10.0050")


@pytest.mark.parametrize("age", ["5.01", "30"])
def test_old_authoritative_refresh_fails_closed(tmp_path, age: str) -> None:
    candidate, signal = evaluate(tmp_path, lambda _symbol: snapshot(age=age))
    assert signal is None
    assert candidate.status.value == "AWAITING_EXECUTION_DATA"


@pytest.mark.parametrize("source", [lambda _symbol: None,
                                     lambda _symbol: (_ for _ in ()).throw(TimeoutError())])
def test_missing_or_failed_refresh_fails_closed(tmp_path, source) -> None:
    _candidate, signal = evaluate(tmp_path, source)
    assert signal is None


def test_refreshed_wide_spread_fails_closed(tmp_path) -> None:
    _candidate, wide = evaluate(tmp_path / "wide", lambda _symbol: snapshot(bid="9", ask="10.0050"))
    assert wide is None


def test_confirmation_crossing_technical_minute_fails_closed(tmp_path) -> None:
    next_minute = NOW + timedelta(minutes=1)
    _candidate, signal = evaluate(tmp_path, lambda _symbol: snapshot(
        confirmed_at=next_minute,
    ))
    assert signal is None


class _SixTwentyFiveTriggerRuntime(WarriorMomentumRuntime):
    def discover(self, observation, bars, *, session, top_gapper=False):
        candidate = super().discover(
            observation, bars, session=session, top_gapper=top_gapper,
        )
        assert candidate.setup is not None
        return replace(candidate, setup=replace(
            candidate.setup, state=SetupState.TRIGGERED,
            trigger=D("6.25"), stop_price=D("5.80"),
        ))


def test_preexisting_limit_semantics_allow_fresh_quote_one_cent_above_trigger(tmp_path) -> None:
    observation = scanner(
        price=D("6.26"), previous_close=D("5.00"),
        bid=D("6.25"), ask=D("6.26"),
    )
    candidate, signal = evaluate(
        tmp_path,
        lambda _symbol: snapshot(last="6.26", bid="6.25", ask="6.26"),
        value=point(
            observation=observation, evaluation_timestamp=NOW,
            quote_freshness_seconds=D("30"),
            last_price_freshness_seconds=D("30"),
        ),
        runtime=_SixTwentyFiveTriggerRuntime(),
    )
    assert candidate.status.value == "ENTRY_READY"
    assert signal is not None
    assert signal.entry_trigger == D("6.25")
    assert signal.reference_price == D("6.26")


def test_no_setup_never_requests_confirmation(tmp_path) -> None:
    calls = []
    _candidate, signal = evaluate(
        tmp_path, lambda symbol: calls.append(symbol) or snapshot(),
        value=point(
            bars=(), evaluation_timestamp=NOW,
            quote_freshness_seconds=D("30"),
            last_price_freshness_seconds=D("30"),
        ),
    )
    assert signal is None and calls == []


def test_shutdown_intent_blocks_late_response(tmp_path) -> None:
    _candidate, signal = evaluate(tmp_path, lambda _symbol: snapshot(), permitted=lambda: False)
    assert signal is None


def test_parser_requires_provider_times_and_bid_ask() -> None:
    epoch = int(NOW.timestamp() * 1000)
    valid = parse_execution_quote("xyz", {
        "price": "9.99", "bid": "9.98", "ask": "10.00",
        "last_trade_time": epoch, "quote_time": epoch,
    }, confirmed_at=NOW)
    assert valid is not None and valid.symbol == "XYZ"
    assert parse_execution_quote("XYZ", {
        "price": "9.99", "bid": "9.98", "ask": "10.00",
        "last_trade_time": epoch,
    }) is None
    assert parse_execution_quote("XYZ", {
        "price": "9.99", "last_trade_time": epoch, "quote_time": epoch,
    }) is None

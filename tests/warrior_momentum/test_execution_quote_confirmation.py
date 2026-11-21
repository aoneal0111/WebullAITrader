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


def snapshot(*, age: str = "1", bid: str = "10.0050", ask: str = "10.0060",
             last: str = "10.0060", confirmed_at=NOW) -> ExecutionQuoteSnapshot:
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
    assert signal is not None and signal.reference_price == D("10.0060")


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
    candidate, wide = evaluate(tmp_path / "wide", lambda _symbol: snapshot(bid="9", ask="10.0050"))
    assert wide is None
    assert candidate.status.value == "AWAITING_EXECUTION_DATA"
    assert "EXECUTION_QUALITY_WAIT" in {
        reason.value for reason in candidate.reason_codes
    }


def test_old_retained_source_age_does_not_masquerade_as_processing_delay(
    tmp_path,
) -> None:
    calls = []
    candidate, signal = evaluate(
        tmp_path,
        lambda symbol: calls.append(symbol) or snapshot(),
        value=point(
            evaluation_timestamp=NOW,
            quote_freshness_seconds=D("30"),
            last_price_freshness_seconds=D("30"),
            processing_age_seconds=D("0.02"),
            delivery_age_seconds=D("30"),
            retained_reevaluation=True,
            retained_source_age_seconds=D("146.248"),
            reevaluation_mailbox_age_seconds=D("0.009"),
        ),
    )
    assert calls == ["XYZ"]
    assert signal is not None
    assert "PROCESSING_DELAYED" not in {
        reason.value for reason in candidate.reason_codes
    }
    assert candidate.decision_generation_id is not None
    assert candidate.decision_timestamp == NOW
    assert candidate.decision_quote_timestamp == snapshot().bid_timestamp


def test_retained_generation_advances_with_fresh_authoritative_quote(tmp_path) -> None:
    initial = point(
        evaluation_timestamp=NOW,
        quote_freshness_seconds=D("30"),
        last_price_freshness_seconds=D("30"),
        processing_age_seconds=D("0.01"),
        delivery_age_seconds=D("30"),
        retained_reevaluation=True,
        retained_source_age_seconds=D("30"),
        reevaluation_mailbox_age_seconds=D("0.01"),
    )
    candidate, signal = evaluate(tmp_path, lambda _symbol: snapshot(), value=initial)
    assert signal is not None
    assert candidate.scanner_observation_timestamp == initial.observation.timestamp
    assert candidate.warrior_observation_timestamp == NOW
    assert candidate.decision_quote_timestamp == snapshot().bid_timestamp


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


def test_processing_delayed_technical_event_fails_before_rest_confirmation(
    tmp_path,
) -> None:
    calls = []
    candidate, signal = evaluate(
        tmp_path,
        lambda symbol: calls.append(symbol) or snapshot(),
        value=point(
            evaluation_timestamp=NOW,
            quote_freshness_seconds=D("0.2"),
            last_price_freshness_seconds=D("0.2"),
            processing_age_seconds=D("5.01"),
        ),
    )
    assert signal is None
    assert calls == []
    assert "PROCESSING_DELAYED" in {
        reason.value for reason in candidate.reason_codes
    }
    assert "AWAITING_EXECUTION_QUOTE" not in {
        reason.value for reason in candidate.reason_codes
    }


def test_upstream_delayed_technical_event_cannot_mix_with_current_rest_quote(
    tmp_path,
) -> None:
    calls = []
    candidate, signal = evaluate(
        tmp_path,
        lambda symbol: calls.append(symbol) or snapshot(),
        value=point(
            evaluation_timestamp=NOW,
            quote_freshness_seconds=D("30"),
            last_price_freshness_seconds=D("30"),
            processing_age_seconds=D("0.1"),
            delivery_age_seconds=D("30"),
        ),
    )
    assert signal is None and calls == []
    assert "PROCESSING_DELAYED" in {
        reason.value for reason in candidate.reason_codes
    }


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


def test_controlled_warrior_refreshes_three_second_observation_before_entry(tmp_path):
    from app.strategies.warrior_momentum.paper_stream_quote import WarriorPaperStreamQuoteSource
    calls=[]
    source=WarriorPaperStreamQuoteSource(lambda s:calls.append(s) or snapshot(),clock=lambda:NOW)
    candidate,signal=evaluate(tmp_path,source,value=point(evaluation_timestamp=NOW,
        quote_freshness_seconds=D(3),last_price_freshness_seconds=D(3)))
    assert calls==['XYZ']
    assert signal is not None and candidate.status.value=='ENTRY_READY'


@pytest.mark.parametrize('field,age,reason',[
    ('ask_timestamp',3,'QUOTE_REJECTED_ASK_STALE'),
    ('ask_timestamp',-.01,'QUOTE_REJECTED_ASK_FUTURE'),
    ('bid_timestamp',-.01,'QUOTE_REJECTED_BID_FUTURE'),
    ('last_timestamp',3,'QUOTE_REJECTED_LAST_TRADE_STALE'),
    ('last_timestamp',-.01,'QUOTE_REJECTED_LAST_TRADE_FUTURE'),
])
def test_controlled_warrior_rejects_each_bad_component_with_exact_reason(tmp_path,monkeypatch,field,age,reason):
    from app.strategies.warrior_momentum.paper_stream_quote import WarriorPaperStreamQuoteSource
    from app.performance_diagnostics import PerformanceDiagnostics
    import app.strategies.warrior_momentum.forward_runtime as module
    diagnostics=PerformanceDiagnostics();monkeypatch.setattr(module,'performance_diagnostics',diagnostics)
    quote=replace(snapshot(),**{field:NOW-timedelta(seconds=age)})
    source=WarriorPaperStreamQuoteSource(lambda s:quote,clock=lambda:NOW)
    _,signal=evaluate(tmp_path,source)
    assert signal is None
    metrics=diagnostics.entry_conversion_metrics()
    assert metrics['execution_gate_counters'][reason]==1


def test_missing_confirmation_is_counted_instead_of_disappearing(tmp_path,monkeypatch):
    from app.performance_diagnostics import PerformanceDiagnostics
    import app.strategies.warrior_momentum.forward_runtime as module
    diagnostics=PerformanceDiagnostics();monkeypatch.setattr(module,'performance_diagnostics',diagnostics)
    _,signal=evaluate(tmp_path,lambda s:None)
    assert signal is None
    assert diagnostics.entry_conversion_metrics()['execution_gate_counters']['QUOTE_REJECTED_MISSING']==1


def test_fresh_confirmed_quote_clears_old_staleness_before_intelligence(tmp_path):
    """A valid current confirmation cannot stay stale in the downstream handoff."""
    store = ForwardCaptureStore(tmp_path / "execution.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, execution_quote_source=lambda _symbol: snapshot(),
    )
    class CaptureHandoff:
        def __init__(self):
            self.requests = []
        def lookup(self, _symbol):
            return None
        def submit(self, _symbol, _identity, request):
            self.requests.append(request)
    handoff = CaptureHandoff()
    service._intelligence_handoff = handoff
    try:
        candidate, signal = service.observe(
            point(evaluation_timestamp=NOW,
                  quote_freshness_seconds=D("30"),
                  last_price_freshness_seconds=D("30")),
            account=account(),
        )
        assert candidate.status.value == "ENTRY_READY"
        assert signal is not None
        assert len(handoff.requests) == 1
        assert handoff.requests[0].stale is False
        assert handoff.requests[0].value.last_price_freshness_seconds == D("1")
    finally:
        writer.close()


@pytest.mark.parametrize("setup,first,ask,expected", [
    ("HIGH_OF_DAY_BREAKOUT", "1.01", "1.11", True),
    ("FLAT_TOP_BREAKOUT", "1.01", "1.11", True),
    ("MOMENTUM_ACCELERATION", "1.01", "1.11", True),
    ("HIGH_OF_DAY_BREAKOUT", "1.01", "1.04", False),
    ("HIGH_OF_DAY_BREAKOUT", "1.01", "1.0404", True),
    ("HIGH_OF_DAY_BREAKOUT", "1.01", "1.00", False),
    ("BULL_FLAG", "1.01", "1.11", False),
    ("HIGH_OF_DAY_BREAKOUT", None, "1.11", False),
])
def test_initial_breakout_anti_chase_uses_first_observation_not_moving_trigger(
    setup, first, ask, expected,
):
    from app.strategies.warrior_momentum.entry_extension import (
        initial_breakout_move_exhausted,
    )
    assert initial_breakout_move_exhausted(
        executable_ask=D(ask),
        first_observation_price=None if first is None else D(first),
        setup_type=setup,
    ) is expected


def test_initial_breakout_anti_chase_blocks_execution_even_if_trigger_renews(
    tmp_path,
):
    """A newer rising trigger must not reset a known 10% move from discovery."""
    store = ForwardCaptureStore(tmp_path / "execution.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    try:
        value = point(
            evaluation_timestamp=NOW,
            processing_age_seconds=D("0.1"),
            delivery_age_seconds=D("0.1"),
        )
        candidate = service.runtime.discover(
            value.observation, value.bars, session=value.session,
        )
        signal = service.runtime.technical_entry_signal(candidate)
        assert signal is not None
        from app.strategies.warrior_momentum.models import SetupType
        signal = replace(
            signal,
            setup_type=SetupType.HIGH_OF_DAY_BREAKOUT,
            structural_entry_trigger=D("10.00"),
            entry_trigger=D("10.00"),
            stop_price=D("9.80"),
        )
        fresh = replace(value, observation=replace(
            value.observation, price=D("10.006"), bid=D("10.005"), ask=D("10.006"),
        ))
        service._setup_lifecycles["XYZ"] = {
            "price_at_first_observation": D("9.00"),
        }
        assert service._execution_entry_signal(fresh, candidate, signal) is None
        service._setup_lifecycles["XYZ"]["price_at_first_observation"] = D("10.00")
        assert service._execution_entry_signal(fresh, candidate, signal) is not None
    finally:
        writer.close()


@pytest.mark.parametrize("changes,expected_exhausted", [
    ({}, True),
    ({"catalyst_confirmed": True}, True),
    ({"catalyst_confirmed": True, "relative_volume": D("3"),
      "velocity_1m": D("0.5"), "velocity_5m": D("2"),
      "acceleration": D("0.2"), "setup_score": D("90")}, False),
    ({"catalyst_confirmed": True, "relative_volume": D("3"),
      "velocity_1m": D("-0.1"), "velocity_5m": D("2"),
      "acceleration": D("0.2"), "setup_score": D("90")}, True),
    ({"catalyst_confirmed": True, "relative_volume": D("3"),
      "velocity_1m": D("0.5"), "velocity_5m": D("2"),
      "acceleration": D("-0.1"), "setup_score": D("90")}, True),
    ({"catalyst_confirmed": True, "relative_volume": D("1.2"),
      "velocity_1m": D("0.5"), "velocity_5m": D("2"),
      "acceleration": D("0.2"), "setup_score": D("90")}, True),
])
def test_catalyst_alone_does_not_excuse_chase_but_confirmed_second_leg_can(
    changes, expected_exhausted,
):
    from app.strategies.warrior_momentum.entry_extension import (
        initial_breakout_move_exhausted,
    )
    assert initial_breakout_move_exhausted(
        executable_ask=D("1.11"), first_observation_price=D("1.01"),
        setup_type="HIGH_OF_DAY_BREAKOUT", **changes,
    ) is expected_exhausted


def test_initial_breakout_does_not_use_prior_day_or_naive_anchor():
    from app.strategies.warrior_momentum.entry_extension import (
        initial_breakout_move_exhausted,
    )
    base = dict(
        executable_ask=D("11"), first_observation_price=D("9"),
        setup_type="HIGH_OF_DAY_BREAKOUT",
        evaluated_at=NOW,
    )
    assert initial_breakout_move_exhausted(
        **base, first_observed_at=NOW - timedelta(days=1),
    ) is False
    assert initial_breakout_move_exhausted(
        **base, first_observed_at=NOW,
    ) is True
    assert initial_breakout_move_exhausted(
        **base, first_observed_at=NOW.replace(tzinfo=None),
    ) is False


def test_working_paper_buy_reprice_uses_same_exhaustion_gate(tmp_path):
    """A rejected late initial breakout cannot re-enter via BUY pursuit."""
    from types import SimpleNamespace
    from app.strategies.warrior_momentum.models import SetupType
    store = ForwardCaptureStore(tmp_path / "execution.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(store, writer)
    attempted = []
    service._paper_entry_replacer = lambda **kwargs: attempted.append(kwargs)
    try:
        value = point(
            evaluation_timestamp=NOW,
            processing_age_seconds=D("0.1"),
            delivery_age_seconds=D("0.1"),
        )
        candidate = service.runtime.discover(
            value.observation, value.bars, session=value.session,
        )
        signal = service.runtime.technical_entry_signal(candidate)
        assert signal is not None
        signal = replace(signal, setup_type=SetupType.HIGH_OF_DAY_BREAKOUT)
        service._setup_lifecycles["XYZ"] = {
            "first_observed_at": NOW,
            "price_at_first_observation": D("9"),
        }
        service._paper["XYZ"] = SimpleNamespace(
            remaining=10, initial_quantity=10, signal=signal,
        )
        gate_calls = []
        original_gate = service._initial_breakout_exhausted
        def observed_gate(*args):
            outcome = original_gate(*args)
            gate_calls.append(outcome)
            return outcome
        service._initial_breakout_exhausted = observed_gate
        service._consider_adaptive_entry_replacement(
            value, candidate, signal, account(),
        )
        assert gate_calls == [True]
        assert attempted == []
        # This guard is not a second structural veto on a developing early entry.
        service._setup_lifecycles["XYZ"]["price_at_first_observation"] = D("10.20")
        assert service._initial_breakout_exhausted(
            value, candidate, signal, D("10.22"),
        ) is False
    finally:
        writer.close()

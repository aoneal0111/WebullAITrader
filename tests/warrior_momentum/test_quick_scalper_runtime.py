from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

from app.momentum_scanner import AssetClass
from app.momentum_scanner.models import CatalystStatus, CatalystType, ScannerObservation
from app.market_data.models import (
    MarketEvent, MarketEventType, QuotePayload,
)
from app.paper_trading.order_book import PaperOrderBook
from app.performance_diagnostics import performance_diagnostics
from app.paper_trading.order_models import (
    OrderRequest, OrderSide, OrderStatus, OrderType, PaperOrder,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    PaperEntryAuthorizationReason, PaperEntryAuthorizationResult,
    PaperExitSubmissionDecision, PaperExitSubmissionState,
)
from app.strategies.warrior_momentum.execution_quote import (
    ExecutionQuoteSnapshot,
)
from app.strategies.warrior_momentum.forward_models import PaperAccountContext
from app.strategies.warrior_momentum.models import MinuteBar
from app.strategies.warrior_momentum.quick_scalper import (
    QuickScalpSnapshot, QuickScalperConfig, StrategyOwner,
)
from app.strategies.warrior_momentum.quick_scalper_runtime import (
    QuickScalperExecutionIntent, QuickScalperPaperRuntimeAdapter,
)
from tests.test_support.session_clock import (
    create_session_paper_composition, session_timestamp,
)
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousPaperExecutionBridge,
)

NOW = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)


class Bridge:
    def __init__(self) -> None:
        self.entries = []
        self.exits = []

    def submit_entry_decision(self, signal, shares, risk, **kwargs):
        self.entries.append((signal, shares, risk, kwargs))
        return SimpleNamespace(
            result=PaperEntryAuthorizationResult.AUTHORIZED,
            reason=PaperEntryAuthorizationReason.AUTHORIZED,
            order_constructed=True,
            placement_decision="SUCCESS",
        )

    def ensure_exit(self, symbol, quantity, price, reason, lifecycle, **kwargs):
        self.exits.append(
            (symbol, quantity, price, reason, lifecycle, kwargs)
        )
        return PaperExitSubmissionDecision(
            PaperExitSubmissionState.SUBMITTED, symbol, lifecycle, reason,
            f"order-{len(self.exits)}",
        )


class Quotes:
    def __init__(self, value: ExecutionQuoteSnapshot | None) -> None:
        self.value = value
        self.calls = 0
        self.decisions = []

    def __call__(self, symbol: str):
        self.calls += 1
        return self.value

    def record_decision(self, **values):
        self.decisions.append(values)


def quote(age: float = 0.2) -> ExecutionQuoteSnapshot:
    stamp = NOW - timedelta(seconds=age)
    return ExecutionQuoteSnapshot(
        "FAST", D("5.00"), D("4.99"), D("5.00"),
        stamp, stamp, stamp, NOW,
    )


def snapshot(generation: str = "g1") -> QuickScalpSnapshot:
    stamp = NOW - timedelta(seconds=0.2)
    return QuickScalpSnapshot(
        "FAST", generation, "QUICK_SCALP_MOMENTUM", NOW, NOW,
        D("5"), D("4.99"), D("5"), stamp, stamp, stamp,
        D("4.90"), D("0.20"), D("0.20"), D("4"),
        D("10"), D("25000000"), D("80"), D("85"), "TRUE", "REGULAR",
    )


def account() -> PaperAccountContext:
    return PaperAccountContext(
        D("100000"), D("100000"), frozenset({"FAST"}),
        exposure_limit=D("50000"),
    )


def adapter(
    *, enabled: bool = True, live: bool = False,
    book: PaperOrderBook | None = None, bridge: Bridge | None = None,
    quotes: Quotes | None = None,
    events: list[tuple[str, dict[str, object]]] | None = None,
) -> tuple[QuickScalperPaperRuntimeAdapter, Bridge, Quotes]:
    bridge = bridge or Bridge()
    quotes = quotes or Quotes(quote())
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=enabled),
        bridge=bridge, order_book=book or PaperOrderBook(),
        account_context_source=account,
        position_quantity_source=lambda _symbol: D("0"),
        execution_quote_source=quotes,
        live_trading_enabled=live, clock=lambda: NOW,
        event_sink=(
            None if events is None
            else lambda event, payload: events.append((event, payload))
        ),
    )
    return runtime, bridge, quotes


def test_stream_assessment_does_not_require_warrior_candidate_or_completed_bar():
    before = performance_diagnostics.snapshot()
    runtime, bridge, _quotes = adapter()
    first_at = NOW - timedelta(seconds=2)
    second_at = NOW
    first = ScannerObservation(
        symbol="FAST", timestamp=first_at, price=D("4.96"),
        previous_close=D("4.00"), current_volume=D("1000000"),
        average_30_day_volume=D("200000"), float_shares=D("5000000"),
        bid=D("4.95"), ask=D("4.96"), catalyst=CatalystType.OTHER,
        catalyst_headline=None, tradable=True, halted=False,
        catalyst_status=CatalystStatus.TRUE,
        last_price_timestamp=first_at, quote_timestamp=first_at,
        trade_timestamp=first_at, bid_size=D("500"), ask_size=D("500"),
    )
    second = ScannerObservation(
        symbol="FAST", timestamp=second_at, price=D("5.01"),
        previous_close=D("4.00"), current_volume=D("1010000"),
        average_30_day_volume=D("200000"), float_shares=D("5000000"),
        bid=D("5.00"), ask=D("5.01"), catalyst=CatalystType.OTHER,
        catalyst_headline=None, tradable=True, halted=False,
        catalyst_status=CatalystStatus.TRUE,
        last_price_timestamp=second_at, quote_timestamp=second_at,
        trade_timestamp=second_at, bid_size=D("500"), ask_size=D("500"),
    )

    assert runtime.observe_stream_event(
        first, decision_at=first_at, session="REGULAR",
        context=None, execution_permitted=False,
    ) is None
    assert runtime.observe_stream_event(
        second, decision_at=second_at, session="REGULAR",
        context=None, execution_permitted=False,
    ) is None

    assert len(runtime._stream_samples["FAST"]) == 2
    assert bridge.entries == []
    after = performance_diagnostics.snapshot()
    assert (
        after.quick_scalper_stream_observations
        - before.quick_scalper_stream_observations
    ) == 2
    assert (
        after.quick_scalper_assessments - before.quick_scalper_assessments
    ) == 2
    # Execution is deliberately disabled in this test. The stream path
    # must still count observations/assessment attempts, but it must not
    # advance an opportunity into the execution lifecycle or request a quote.
    assert (
        after.quick_scalper_opportunities - before.quick_scalper_opportunities
    ) == 0
    assert (
        after.quick_scalper_authorization_attempts
        - before.quick_scalper_authorization_attempts
    ) == 0


def test_initial_stream_wait_reason_is_attributed_exactly():
    before = performance_diagnostics.snapshot()
    runtime, bridge, quotes = adapter()

    first_at = NOW - timedelta(seconds=2)
    second_at = NOW
    first = ScannerObservation(
        symbol="FAST", timestamp=first_at, price=D("4.99"),
        previous_close=D("4.00"), current_volume=D("1000000"),
        average_30_day_volume=D("200000"), float_shares=D("5000000"),
        bid=D("4.98"), ask=D("4.99"), catalyst=CatalystType.OTHER,
        catalyst_headline=None, tradable=True, halted=False,
        catalyst_status=CatalystStatus.TRUE,
        last_price_timestamp=first_at, quote_timestamp=first_at,
        trade_timestamp=first_at, bid_size=D("500"), ask_size=D("500"),
    )
    second = ScannerObservation(
        symbol="FAST", timestamp=second_at, price=D("5.00"),
        previous_close=D("4.00"), current_volume=D("1010000"),
        average_30_day_volume=D("200000"), float_shares=D("5000000"),
        bid=D("4.99"), ask=D("5.00"), catalyst=CatalystType.OTHER,
        catalyst_headline=None, tradable=True, halted=False,
        catalyst_status=CatalystStatus.TRUE,
        last_price_timestamp=second_at, quote_timestamp=second_at,
        trade_timestamp=second_at, bid_size=D("500"), ask_size=D("500"),
    )

    assert runtime.observe_stream_event(
        first, decision_at=first_at, session="REGULAR",
        context=None, execution_permitted=True,
    ) is None
    result = runtime.observe_stream_event(
        second, decision_at=second_at, session="REGULAR",
        context=None, execution_permitted=True,
    )

    assert result is not None
    assert result.reason == "INSUFFICIENT_NET_EXECUTABLE_EDGE"
    assert quotes.calls == 0
    assert bridge.entries == []
    after = performance_diagnostics.snapshot()
    assert (
        after.quick_scalper_opportunities
        - before.quick_scalper_opportunities
    ) == 1
    assert (
        after.quick_scalper_executable - before.quick_scalper_executable
    ) == 0
    assert (
        after.quick_scalper_initial_insufficient_edge
        - before.quick_scalper_initial_insufficient_edge
    ) == 1
    assert (
        after.quick_scalper_authorization_attempts
        - before.quick_scalper_authorization_attempts
    ) == 0


def test_initial_stream_hard_safety_reason_is_attributed_exactly():
    before = performance_diagnostics.snapshot()
    runtime, bridge, quotes = adapter()

    first_at = NOW - timedelta(seconds=7)
    second_at = NOW
    first = ScannerObservation(
        symbol="FAST", timestamp=first_at, price=D("4.96"),
        previous_close=D("4.00"), current_volume=D("1000000"),
        average_30_day_volume=D("200000"), float_shares=D("5000000"),
        bid=D("4.95"), ask=D("4.96"), catalyst=CatalystType.OTHER,
        catalyst_headline=None, tradable=True, halted=False,
        catalyst_status=CatalystStatus.TRUE,
        last_price_timestamp=first_at, quote_timestamp=first_at,
        trade_timestamp=first_at, bid_size=D("500"), ask_size=D("500"),
    )
    second = ScannerObservation(
        symbol="FAST", timestamp=second_at, price=D("5.01"),
        previous_close=D("4.00"), current_volume=D("1010000"),
        average_30_day_volume=D("200000"), float_shares=D("5000000"),
        bid=D("5.00"), ask=D("5.01"), catalyst=CatalystType.OTHER,
        catalyst_headline=None, tradable=True, halted=False,
        catalyst_status=CatalystStatus.TRUE,
        last_price_timestamp=first_at, quote_timestamp=second_at,
        trade_timestamp=first_at, bid_size=D("500"), ask_size=D("500"),
    )

    assert runtime.observe_stream_event(
        first, decision_at=first_at, session="REGULAR",
        context=None, execution_permitted=True,
    ) is None
    result = runtime.observe_stream_event(
        second, decision_at=second_at, session="REGULAR",
        context=None, execution_permitted=True,
    )

    assert result is not None
    assert result.reason == "PROVIDER_DATA_STALE"
    assert quotes.calls == 0
    assert bridge.entries == []
    after = performance_diagnostics.snapshot()
    assert (
        after.quick_scalper_initial_provider_data_stale
        - before.quick_scalper_initial_provider_data_stale
    ) == 1
    assert (
        after.quick_scalper_rejections - before.quick_scalper_rejections
    ) == 1


def test_canonical_scalp_intent_submits_once_without_warrior_signal_coercion():
    before = performance_diagnostics.snapshot()
    runtime, bridge, quotes = adapter()
    value = snapshot()
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    runtime.scalper.observe(value)

    assert quotes.calls == 1
    assert len(bridge.entries) == 1
    intent, shares, risk, values = bridge.entries[0]
    assert isinstance(intent, QuickScalperExecutionIntent)
    assert intent.strategy_owner == "QUICK_SCALPER"
    assert intent.strategy_id == "QUICK_SCALPER"
    assert shares > 0 and risk > 0
    assert values["provenance"] == "QUICK_SCALPER_CANONICAL_ENTRY"
    after = performance_diagnostics.snapshot()
    assert (
        after.quick_scalper_authorization_attempts
        - before.quick_scalper_authorization_attempts
    ) == 1
    assert (
        after.quick_scalper_orders_submitted
        - before.quick_scalper_orders_submitted
    ) == 1


def test_lifecycle_observability_precedes_authorization_without_extra_quote():
    events = []
    runtime, bridge, quotes = adapter(events=events)
    runtime._snapshot = lambda _point, _candidate: snapshot()
    runtime.observe(
        SimpleNamespace(
            observation=SimpleNamespace(bid=None, quote_timestamp=None),
            evaluation_timestamp=NOW, quote_observed_at=None,
        ),
        SimpleNamespace(symbol="FAST"),
    )
    names = [event for event, _payload in events]
    assert names[:4] == [
        "SCALP_ASSESSED",
        "SCALP_OPPORTUNITY", "SCALP_ARMED", "SCALP_EXECUTABLE",
    ]
    assert names[4:] == [
        "SCALP_ORDER_INTENT", "SCALP_AUTHORIZED", "SCALP_ORDER_SUBMITTED",
    ]
    assert quotes.calls == 1
    assert len(bridge.entries) == 1


def test_warrior_soft_score_does_not_preempt_scalper_assessment():
    runtime, bridge, quotes = adapter()
    runtime._snapshot = lambda _point, _candidate: snapshot()
    result = runtime.observe(
        SimpleNamespace(
            observation=SimpleNamespace(bid=None, quote_timestamp=None),
            evaluation_timestamp=NOW, quote_observed_at=None,
        ),
        SimpleNamespace(
            symbol="FAST",
            score=SimpleNamespace(total=D("52.48")),
            setup=None,
        ),
    )
    assert result is not None
    assert result.opportunity.momentum_priority == D("85")
    assert quotes.calls == 1
    assert len(bridge.entries) == 1


def test_scalper_builds_real_snapshot_while_warrior_setup_is_only_forming():
    runtime, bridge, quotes = adapter()
    bars = tuple(
        MinuteBar(
            "FAST", NOW - timedelta(minutes=3 - index),
            D(open_price), D(high), D(low), D(close), D("10000"),
        )
        for index, (open_price, high, low, close) in enumerate((
            ("4.82", "4.88", "4.80", "4.86"),
            ("4.86", "4.95", "4.84", "4.93"),
            ("4.93", "5.01", "4.90", "5.00"),
        ))
    )
    point = SimpleNamespace(
        observation=SimpleNamespace(
            symbol="FAST", timestamp=NOW, price=D("5.00"),
            bid=D("4.99"), ask=D("5.00"),
            last_price_timestamp=NOW - timedelta(seconds=0.2),
            quote_timestamp=NOW - timedelta(seconds=0.2),
        ),
        evaluation_timestamp=NOW,
        last_price_observed_at=NOW - timedelta(seconds=0.2),
        quote_observed_at=NOW - timedelta(seconds=0.2),
        bars=bars,
        session="REGULAR",
    )
    candidate = SimpleNamespace(
        symbol="FAST", observation_eligible=False, tradable=True, halted=False,
        price_velocity_cents_1m=D("0.20"),
        price_velocity_percent_1m=D("4"),
        relative_volume=D("0"), dollar_volume=D("1000"),
        score=SimpleNamespace(total=D("52.48")), momentum_priority=D("85"),
        catalyst_status=SimpleNamespace(value="TRUE"),
        setup=SimpleNamespace(
            state=SimpleNamespace(value="FORMING"), score=D("70"),
            structural_episode_id="warrior-forming-only",
        ),
    )

    result = runtime.observe(point, candidate)

    assert result is not None
    assert result.opportunity.strategy == "QUICK_SCALPER"
    assert quotes.calls == 1
    assert len(bridge.entries) == 1


def test_warrior_invalidation_does_not_invalidate_independent_scalp_generation():
    runtime, bridge, quotes = adapter()
    runtime._current_generation["FAST"] = "scalp-g1"
    runtime._snapshot = lambda _point, _candidate: None

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Warrior setup state must not own Scalper lifecycle")

    runtime.engine.invalidate = fail_if_called
    result = runtime.observe(
        SimpleNamespace(
            observation=SimpleNamespace(bid=None, quote_timestamp=None),
            evaluation_timestamp=NOW, quote_observed_at=None,
        ),
        SimpleNamespace(
            symbol="FAST",
            setup=SimpleNamespace(state=SimpleNamespace(value="INVALIDATED")),
        ),
    )

    assert result is None
    assert runtime._current_generation["FAST"] == "scalp-g1"
    assert quotes.calls == 0
    assert bridge.entries == []


def test_known_weak_top_of_book_liquidity_reduces_canonical_quantity():
    runtime, bridge, _quotes = adapter()
    value = replace(snapshot(), executable_ask_size=D('40'))
    runtime._snapshots[('FAST', 'g1')] = value

    runtime.scalper.observe(value)

    assert len(bridge.entries) == 1
    assert bridge.entries[0][1] == 10


def test_high_price_scalp_sizes_to_buying_power_instead_of_price_veto():
    stamp = NOW - timedelta(seconds=0.2)
    high_quote = ExecutionQuoteSnapshot(
        'FAST', D('500'), D('499.50'), D('500'),
        stamp, stamp, stamp, NOW,
    )
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=PaperOrderBook(),
        account_context_source=lambda: PaperAccountContext(
            D('10000'), D('1000'), frozenset({'FAST'}),
            exposure_limit=D('1000'),
        ),
        position_quantity_source=lambda _symbol: D('0'),
        execution_quote_source=Quotes(high_quote), clock=lambda: NOW,
    )
    value = replace(
        snapshot(), last=D('500'), bid=D('499.50'), ask=D('500'),
        structural_stop=D('490'), short_horizon_range=D('4'),
        velocity_cents_per_minute=D('3'),
        velocity_percent_per_minute=D('0.60'),
    )
    runtime._snapshots[('FAST', 'g1')] = value

    runtime.scalper.observe(value)

    assert len(bridge.entries) == 1
    assert bridge.entries[0][1] == 2


def test_prebridge_attribution_counters_distinguish_quote_risk_and_bridge():
    # Quote unavailable: authorization was attempted but never reached bridge.
    before = performance_diagnostics.snapshot()
    runtime, bridge, _quotes = adapter(quotes=Quotes(None))
    value = snapshot()
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    after = performance_diagnostics.snapshot()
    assert (
        after.quick_scalper_execution_quote_unavailable
        - before.quick_scalper_execution_quote_unavailable
    ) == 1
    assert (
        after.quick_scalper_bridge_reached - before.quick_scalper_bridge_reached
    ) == 0
    assert bridge.entries == []

    # Risk rejection: fresh quote exists but account sizing fails before bridge.
    stamp = NOW - timedelta(seconds=0.2)
    high_quote = ExecutionQuoteSnapshot(
        "FAST", D("500"), D("499.50"), D("500"),
        stamp, stamp, stamp, NOW,
    )
    before = performance_diagnostics.snapshot()
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=PaperOrderBook(),
        account_context_source=lambda: PaperAccountContext(
            D("1000"), D("499"), frozenset({"FAST"}),
            exposure_limit=D("1000"),
        ),
        position_quantity_source=lambda _symbol: D("0"),
        execution_quote_source=Quotes(high_quote), clock=lambda: NOW,
    )
    value = replace(
        snapshot(), last=D("500"), bid=D("499.50"), ask=D("500"),
        structural_stop=D("490"), short_horizon_range=D("4"),
        velocity_cents_per_minute=D("3"),
        velocity_percent_per_minute=D("0.60"),
    )
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    after = performance_diagnostics.snapshot()
    assert (
        after.quick_scalper_risk_rejected - before.quick_scalper_risk_rejected
    ) == 1
    assert (
        after.quick_scalper_bridge_reached - before.quick_scalper_bridge_reached
    ) == 0
    assert bridge.entries == []

    # Happy path: bridge boundary and authorization are counted exactly once.
    before = performance_diagnostics.snapshot()
    runtime, bridge, _quotes = adapter()
    value = snapshot()
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    after = performance_diagnostics.snapshot()
    assert (
        after.quick_scalper_bridge_reached - before.quick_scalper_bridge_reached
    ) == 1
    assert (
        after.quick_scalper_bridge_authorized
        - before.quick_scalper_bridge_authorized
    ) == 1
    assert (
        after.quick_scalper_bridge_rejected
        - before.quick_scalper_bridge_rejected
    ) == 0
    assert len(bridge.entries) == 1


def test_reconfirm_reason_counter_attributes_exact_policy_reason():
    # The stream snapshot is executable, but the authoritative confirmation
    # widens the spread enough that expected move no longer covers execution
    # cost. This exercises the real reconfirmation branch rather than failing
    # before authorization begins.
    stamp = NOW - timedelta(seconds=0.2)
    widened_quote = ExecutionQuoteSnapshot(
        "FAST", D("5.00"), D("4.70"), D("5.00"),
        stamp, stamp, stamp, NOW,
    )
    before = performance_diagnostics.snapshot()
    runtime, bridge, quotes = adapter(quotes=Quotes(widened_quote))
    value = snapshot()
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    after = performance_diagnostics.snapshot()

    assert quotes.calls == 1
    assert (
        after.quick_scalper_authorization_attempts
        - before.quick_scalper_authorization_attempts
    ) == 1
    assert (
        after.quick_scalper_reconfirm_not_executable
        - before.quick_scalper_reconfirm_not_executable
    ) == 1
    assert (
        after.quick_scalper_reconfirm_insufficient_edge
        - before.quick_scalper_reconfirm_insufficient_edge
    ) == 1
    assert (
        after.quick_scalper_bridge_reached - before.quick_scalper_bridge_reached
    ) == 0
    assert bridge.entries == []


def test_stale_confirmation_attributes_last_only_component():
    stamp = NOW - timedelta(seconds=0.2)
    stale_last = NOW - timedelta(seconds=6)
    quote = ExecutionQuoteSnapshot(
        "FAST", D("5.00"), D("4.99"), D("5.00"),
        stale_last, stamp, stamp, NOW,
    )
    before = performance_diagnostics.snapshot()
    runtime, bridge, _quotes = adapter(quotes=Quotes(quote))
    value = snapshot()
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    after = performance_diagnostics.snapshot()

    assert (
        after.quick_scalper_reconfirm_provider_data_stale
        - before.quick_scalper_reconfirm_provider_data_stale
    ) == 1
    assert (
        after.quick_scalper_freshness_last_stale
        - before.quick_scalper_freshness_last_stale
    ) == 1
    assert (
        after.quick_scalper_freshness_last_only
        - before.quick_scalper_freshness_last_only
    ) == 1
    assert (
        after.quick_scalper_freshness_bid_stale
        - before.quick_scalper_freshness_bid_stale
    ) == 0
    assert (
        after.quick_scalper_freshness_ask_stale
        - before.quick_scalper_freshness_ask_stale
    ) == 0
    assert bridge.entries == []


def test_stale_confirmation_attributes_multiple_components():
    stale = NOW - timedelta(seconds=7)
    quote = ExecutionQuoteSnapshot(
        "FAST", D("5.00"), D("4.99"), D("5.00"),
        stale, stale, stale, NOW,
    )
    before = performance_diagnostics.snapshot()
    runtime, bridge, _quotes = adapter(quotes=Quotes(quote))
    value = snapshot()
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    after = performance_diagnostics.snapshot()

    assert (
        after.quick_scalper_freshness_multiple
        - before.quick_scalper_freshness_multiple
    ) == 1
    assert (
        after.quick_scalper_freshness_last_stale
        - before.quick_scalper_freshness_last_stale
    ) == 1
    assert (
        after.quick_scalper_freshness_bid_stale
        - before.quick_scalper_freshness_bid_stale
    ) == 1
    assert (
        after.quick_scalper_freshness_ask_stale
        - before.quick_scalper_freshness_ask_stale
    ) == 1
    assert bridge.entries == []


def test_risk_diagnostic_counter_attributes_zero_share_rejection():
    stamp = NOW - timedelta(seconds=0.2)
    high_quote = ExecutionQuoteSnapshot(
        "FAST", D("500"), D("499.50"), D("500"),
        stamp, stamp, stamp, NOW,
    )
    before = performance_diagnostics.snapshot()
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=PaperOrderBook(),
        account_context_source=lambda: PaperAccountContext(
            D("1000"), D("499"), frozenset({"FAST"}),
            exposure_limit=D("1000"),
        ),
        position_quantity_source=lambda _symbol: D("0"),
        execution_quote_source=Quotes(high_quote), clock=lambda: NOW,
    )
    value = replace(
        snapshot(), last=D("500"), bid=D("499.50"), ask=D("500"),
        structural_stop=D("490"), short_horizon_range=D("4"),
        velocity_cents_per_minute=D("3"),
        velocity_percent_per_minute=D("0.60"),
    )
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    after = performance_diagnostics.snapshot()

    assert (
        after.quick_scalper_risk_rejected - before.quick_scalper_risk_rejected
    ) == 1
    assert (
        after.quick_scalper_risk_zero_shares
        - before.quick_scalper_risk_zero_shares
    ) == 1
    assert bridge.entries == []


def test_insufficient_buying_power_still_rejects_high_price_scalp():
    stamp = NOW - timedelta(seconds=0.2)
    high_quote = ExecutionQuoteSnapshot(
        'FAST', D('500'), D('499.50'), D('500'),
        stamp, stamp, stamp, NOW,
    )
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=PaperOrderBook(),
        account_context_source=lambda: PaperAccountContext(
            D('1000'), D('499'), frozenset({'FAST'}),
            exposure_limit=D('1000'),
        ),
        position_quantity_source=lambda _symbol: D('0'),
        execution_quote_source=Quotes(high_quote), clock=lambda: NOW,
    )
    value = replace(
        snapshot(), last=D('500'), bid=D('499.50'), ask=D('500'),
        structural_stop=D('490'), short_horizon_range=D('4'),
        velocity_cents_per_minute=D('3'),
        velocity_percent_per_minute=D('0.60'),
    )
    runtime._snapshots[('FAST', 'g1')] = value

    runtime.scalper.observe(value)

    assert bridge.entries == []


def test_disabled_and_live_modes_are_inert_and_make_no_quote_request():
    disabled, bridge, quotes = adapter(enabled=False)
    disabled._snapshots[("FAST", "g1")] = snapshot()
    disabled.scalper.observe(snapshot())
    assert quotes.calls == 0 and bridge.entries == []

    live, live_bridge, live_quotes = adapter(enabled=True, live=True)
    live._snapshots[("FAST", "g1")] = snapshot()
    live.scalper.observe(snapshot())
    assert live_quotes.calls == 0 and live_bridge.entries == []


def paper_order(
    *, order_id: str, side: OrderSide, quantity: str, filled: str,
    status: OrderStatus, reason: str, lifecycle: str = "life",
) -> PaperOrder:
    fill_qty = D(filled)
    return PaperOrder(
        order_id,
        OrderRequest(
            "FAST", AssetClass.STOCK, side,
            OrderType.LIMIT if side is OrderSide.BUY else OrderType.LIMIT,
            D(quantity), limit_price=D("5") if side is OrderSide.BUY else D("5.20"),
            strategy_lifecycle_id=lifecycle, execution_reason=reason,
            metadata={
                "strategy_owner": "QUICK_SCALPER",
                "structural_stop": "4.90", "adaptive_target": "5.20",
                "opportunity_id": "g1",
            },
        ),
        status, NOW, NOW, fill_qty,
        D("5") if fill_qty > 0 and side is OrderSide.BUY else
        D("5.20") if fill_qty > 0 else None,
    )


def test_partial_fill_reconciliation_protects_actual_quantity_and_releases():
    book = PaperOrderBook()
    book.submit(paper_order(
        order_id="entry", side=OrderSide.BUY, quantity="100", filled="10",
        status=OrderStatus.PARTIALLY_FILLED, reason="ENTRY",
    ))
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=book, account_context_source=account,
        position_quantity_source=lambda _symbol: D("10"),
        execution_quote_source=Quotes(quote()), clock=lambda: NOW,
    )
    assert [item[1] for item in bridge.exits[-2:]] == [10, 10]
    assert [item[3] for item in bridge.exits[-2:]] == ["STOP", "SCALP_TARGET"]
    assert runtime.ownership.owner("FAST") == (
        StrategyOwner.QUICK_SCALPER, "life",
    )

    book.update(paper_order(
        order_id="entry", side=OrderSide.BUY, quantity="100", filled="40",
        status=OrderStatus.PARTIALLY_FILLED, reason="ENTRY",
    ))
    runtime.reconcile("FAST")
    assert [item[1] for item in bridge.exits[-2:]] == [40, 40]


def test_restart_reconstruction_ignores_warrior_and_restores_scalper_owner():
    book = PaperOrderBook()
    quick = paper_order(
        order_id="quick", side=OrderSide.BUY, quantity="10", filled="10",
        status=OrderStatus.FILLED, reason="ENTRY", lifecycle="quick-life",
    )
    book.submit(quick)
    warrior = PaperOrder(
        "warrior",
        OrderRequest(
            "OTHER", AssetClass.STOCK, OrderSide.BUY, OrderType.LIMIT, D("1"),
            limit_price=D("10"), strategy_lifecycle_id="warrior-life",
            execution_reason="ENTRY",
            metadata={"strategy_owner": "WARRIOR_MOMENTUM"},
        ),
        OrderStatus.ACCEPTED, NOW, NOW,
    )
    book.submit(warrior)
    runtime, _, _ = adapter(book=book)
    assert runtime.ownership.owner("FAST") == (
        StrategyOwner.QUICK_SCALPER, "quick-life",
    )
    assert runtime.ownership.owner("OTHER") is None


def test_max_hold_profit_retention_uses_canonical_exit_path():
    book = PaperOrderBook()
    book.submit(paper_order(
        order_id="entry", side=OrderSide.BUY, quantity="10", filled="10",
        status=OrderStatus.FILLED, reason="ENTRY",
    ))
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=book, account_context_source=account,
        position_quantity_source=lambda _symbol: D("10"),
        execution_quote_source=Quotes(quote()), clock=lambda: NOW,
    )
    bridge.exits.clear()
    later = NOW + timedelta(seconds=301)
    point = SimpleNamespace(
        observation=SimpleNamespace(bid=D("5.03"), quote_timestamp=later),
        evaluation_timestamp=later, quote_observed_at=later,
    )
    candidate = SimpleNamespace(
        symbol="FAST", price_velocity_cents_1m=D("0"),
    )
    runtime._manage_open_position(point, candidate)
    assert bridge.exits[-1][2:4] == (D("5.03"), "SCALP_MAX_HOLD")
    assert bridge.exits[-1][-1]["strategy_owner"] == "QUICK_SCALPER"


def test_momentum_stall_profit_exit_uses_canonical_exit_path():
    book = PaperOrderBook()
    book.submit(paper_order(
        order_id="entry", side=OrderSide.BUY, quantity="10", filled="10",
        status=OrderStatus.FILLED, reason="ENTRY",
    ))
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=book, account_context_source=account,
        position_quantity_source=lambda _symbol: D("10"),
        execution_quote_source=Quotes(quote()), clock=lambda: NOW,
    )
    bridge.exits.clear()
    point = SimpleNamespace(
        observation=SimpleNamespace(bid=D("5.06"), quote_timestamp=NOW),
        evaluation_timestamp=NOW, quote_observed_at=NOW,
    )
    candidate = SimpleNamespace(
        symbol="FAST", price_velocity_cents_1m=D("0"),
    )
    runtime._manage_open_position(point, candidate)
    assert bridge.exits[-1][2:4] == (D("5.06"), "SCALP_MOMENTUM_STALL")
    assert bridge.exits[-1][-1]["strategy_owner"] == "QUICK_SCALPER"


def test_mfe_giveback_tightens_canonical_stop_without_raw_exit_path():
    book = PaperOrderBook()
    book.submit(paper_order(
        order_id="entry", side=OrderSide.BUY, quantity="10", filled="10",
        status=OrderStatus.FILLED, reason="ENTRY",
    ))
    bridge = Bridge()
    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=book, account_context_source=account,
        position_quantity_source=lambda _symbol: D("10"),
        execution_quote_source=Quotes(quote()), clock=lambda: NOW,
    )
    bridge.exits.clear()
    candidate = SimpleNamespace(
        symbol="FAST", price_velocity_cents_1m=D("0.01"),
    )
    for offset, bid in ((1, D("5.10")), (2, D("5.06"))):
        stamp = NOW + timedelta(seconds=offset)
        runtime._manage_open_position(
            SimpleNamespace(
                observation=SimpleNamespace(bid=bid, quote_timestamp=stamp),
                evaluation_timestamp=stamp, quote_observed_at=stamp,
            ),
            candidate,
        )
    assert bridge.exits[-1][2] == D("5.06")
    assert bridge.exits[-1][3] == "STOP"
    assert bridge.exits[-1][-1]["strategy_owner"] == "QUICK_SCALPER"


def test_stale_authoritative_quote_fails_closed_without_entry():
    runtime, bridge, quotes = adapter(quotes=Quotes(quote(age=5.001)))
    value = snapshot()
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    assert quotes.calls == 1
    assert bridge.entries == []
    assert quotes.decisions[-1]["rejection_reason"] == "PROVIDER_DATA_STALE"


def test_jagx_shape_real_gateway_partial_fill_target_stop_and_release():
    position = {"qty": D("0")}

    def sink(event):
        fill = getattr(event, "fill", None)
        if fill is None:
            return
        position["qty"] += (
            fill.quantity if fill.side == "BUY" else -fill.quantity
        )

    composition = create_session_paper_composition(
        event_sink=sink,
        position_quantity_source=lambda _symbol: position["qty"],
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        mode="PAPER", enabled=True, order_book=composition.order_book,
        protection_amender=composition.gateway.amend_protective_stop,
        position_quantity_source=lambda _symbol: position["qty"],
    )
    stamp = session_timestamp()
    intent = QuickScalperExecutionIntent(
        symbol="JAGX", timestamp=stamp, session="REGULAR",
        entry_trigger=D("5.46"), reference_price=D("5.46"),
        stop_price=D("5.19"), risk_per_share=D("0.27"),
        adaptive_target=D("5.62"), lifecycle_id="scalp-jagx",
        opportunity_id="scalp-jagx-opportunity", generation_id="g-jagx",
        authorization_id="auth-jagx", execution_quote_id="quote-jagx",
        execution_quote_timestamp=stamp, provider_last_timestamp=stamp,
        provider_bid_timestamp=stamp, provider_ask_timestamp=stamp,
    )
    assert bridge.submit_entry_decision(
        intent, 181, D("48.87"),
        opportunity_id=intent.opportunity_id,
        opportunity_anchor=intent.entry_trigger,
        provenance="QUICK_SCALPER_CANONICAL_ENTRY",
    ).authorized
    composition.gateway.process_market_event(MarketEvent(
        1, session_timestamp(1), "JAGX", "test", MarketEventType.QUOTE,
        QuotePayload(D("5.45"), D("5.46"), D("100"), D("10")),
    ))
    assert position["qty"] == D("10")

    runtime = QuickScalperPaperRuntimeAdapter(
        config=QuickScalperConfig(enabled=True), bridge=bridge,
        order_book=composition.order_book, account_context_source=account,
        position_quantity_source=lambda _symbol: position["qty"],
        execution_quote_source=Quotes(quote()), clock=lambda: session_timestamp(2),
    )
    sells = [
        order for order in composition.order_book.open_orders_for_symbol("JAGX")
        if order.request.side is OrderSide.SELL
    ]
    assert {order.request.execution_reason for order in sells} == {
        "STOP", "SCALP_TARGET",
    }
    assert all(order.remaining_quantity == D("10") for order in sells)
    assert all(
        order.request.metadata["strategy_owner"] == "QUICK_SCALPER"
        for order in sells
    )

    composition.gateway.process_market_event(MarketEvent(
        2, session_timestamp(2), "JAGX", "test", MarketEventType.QUOTE,
        QuotePayload(D("5.62"), D("5.63"), D("10"), D("100")),
    ))
    runtime.reconcile("JAGX")
    assert position["qty"] == D("0")
    assert composition.order_book.open_orders_for_symbol("JAGX") == ()
    assert runtime.ownership.owner("JAGX") is None
    assert sum(
        (order.filled_quantity for order in composition.order_book.history()
         if order.symbol == "JAGX" and order.request.side is OrderSide.SELL),
        D("0"),
    ) == D("10")
    composition.close()

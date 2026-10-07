from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.strategies.warrior_momentum.opportunity_engine import (
    WarriorOpportunityEngine, WarriorOpportunityState,
)
from app.strategies.warrior_momentum.profit_retention import (
    ProfitAction, ProfitRetentionPolicy, ProfitRetentionPosition,
)
from app.strategies.warrior_momentum.quick_scalper import (
    CanonicalScalpSubmission, QuickScalpSnapshot, QuickScalper,
    QuickScalperConfig, QuickScalperGuard, QuickScalperPolicy, ScalpDecision,
    StrategyOwner, SymbolOwnershipRegistry,
)

NOW = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)


def snapshot(
    *, symbol: str = "FAST", generation: str = "g1", ask: str = "5.00",
    bid: str = "4.96", stop: str = "4.85", short_range: str = "0.18",
    velocity: str = "0.14", velocity_pct: str = "2.8",
    age: float = 1.0, session: str = "REGULAR",
) -> QuickScalpSnapshot:
    stamp = NOW - timedelta(seconds=age)
    return QuickScalpSnapshot(
        symbol, generation, "MICRO_REACCELERATION", NOW, NOW,
        D(ask), D(bid), D(ask), stamp, stamp, stamp, D(stop),
        D(short_range), D(velocity), D(velocity_pct), D("10"),
        D("25000000"), D("80"), D("85"), "TRUE", session,
    )


def test_adaptive_targets_scale_with_price_volatility_and_cost() -> None:
    policy = QuickScalperPolicy()
    low = policy.assess(snapshot(ask="1.50", bid="1.49", stop="1.43",
                                 short_range="0.06", velocity="0.08"))
    five = policy.assess(snapshot())
    ten = policy.assess(snapshot(ask="10", bid="9.94", stop="9.70",
                                  short_range="0.30", velocity="0.35"))
    high = policy.assess(snapshot(ask="75", bid="74.80", stop="74",
                                   short_range="1.20", velocity="1.40"))
    assert low.target_move >= D("0.05")
    assert low.target_move < five.target_move < ten.target_move < high.target_move


def test_scalper_price_policy_is_one_dollar_minimum_with_no_maximum() -> None:
    policy = QuickScalperPolicy()
    low = policy.assess(snapshot(
        ask='1.01', bid='1.00', stop='0.94', short_range='0.12',
        velocity='0.10', velocity_pct='9.9',
    ))
    high = policy.assess(snapshot(
        ask='500.00', bid='499.50', stop='490.00', short_range='4.00',
        velocity='3.00', velocity_pct='0.60',
    ))
    below = policy.assess(snapshot(
        ask='0.99', bid='0.98', stop='0.90', short_range='0.12',
        velocity='0.10', velocity_pct='10',
    ))
    assert low.decision is ScalpDecision.EXECUTABLE
    assert high.decision is ScalpDecision.EXECUTABLE
    assert below.decision is ScalpDecision.REJECTED_HARD_SAFETY
    assert below.reason == 'PRICE_NOT_ELIGIBLE'
    assert policy.config.minimum_price == D('1.00')
    assert policy.config.maximum_price is None


def test_unavailable_rvol_and_low_liquidity_are_context_not_policy_vetoes() -> None:
    value = snapshot(
        ask='5.00', bid='4.98', stop='4.85', short_range='0.24',
        velocity='0.12',
    )
    value = replace(
        value, relative_volume=D('0'),
        relative_volume_status='EXTENDED_HOURS_HISTORY_UNAVAILABLE',
        dollar_liquidity=D('1000'), executable_ask_size=D('8'),
    )
    assessed = QuickScalperPolicy().assess(value)
    deep = QuickScalperPolicy().assess(replace(
        value, executable_ask_size=D('100'),
    ))
    assert assessed.decision is ScalpDecision.EXECUTABLE
    assert value.relative_volume_status == 'EXTENDED_HOURS_HISTORY_UNAVAILABLE'
    assert assessed.round_trip_cost > deep.round_trip_cost


def test_negative_executable_economics_waits_even_if_last_rises() -> None:
    assessed = QuickScalperPolicy().assess(snapshot(
        ask="5.40", bid="5.18", stop="5.17", short_range="0.14",
        velocity="0.10",
    ))
    assert assessed.decision is ScalpDecision.WAIT
    assert assessed.target_move > D("0.10")


def test_positive_executable_economics_cover_spread_and_risk() -> None:
    assessed = QuickScalperPolicy().assess(snapshot(
        ask="5.00", bid="4.98", stop="4.85", short_range="0.24",
        velocity="0.12",
    ))
    assert assessed.decision is ScalpDecision.EXECUTABLE
    assert assessed.target_price >= D("5.12")
    assert assessed.expected_move >= assessed.target_move


def test_same_spread_percent_is_adaptive_to_expected_movement() -> None:
    quiet = QuickScalperPolicy().assess(snapshot(
        ask="5.00", bid="4.95", stop="4.85", short_range="0.05",
        velocity="0.04", velocity_pct="0.5",
    ))
    active = QuickScalperPolicy().assess(snapshot(
        ask="5.00", bid="4.95", stop="4.85", short_range="0.30",
        velocity="0.25", velocity_pct="5.0",
    ))
    assert quiet.decision is ScalpDecision.WAIT
    assert active.decision is ScalpDecision.EXECUTABLE
    assert quiet.opportunity.spread_percent == active.opportunity.spread_percent


def test_execution_wait_recovers_on_same_scalp_generation() -> None:
    submitted = []
    engine = WarriorOpportunityEngine()
    scalper = QuickScalper(
        engine=engine, ownership=SymbolOwnershipRegistry(),
        config=QuickScalperConfig(enabled=True),
        authorize_and_submit=lambda value: (
            submitted.append(value) or CanonicalScalpSubmission(
                True, True, True, False, "AUTHORIZED",
            )
        ),
    )
    waiting = scalper.observe(snapshot(
        generation="stable", ask="5.00", bid="4.90",
        short_range="0.05", velocity="0.04",
    ))
    executable = scalper.observe(snapshot(
        generation="stable", ask="5.00", bid="4.98",
        short_range="0.24", velocity="0.12",
    ))
    assert waiting.decision is ScalpDecision.WAIT
    assert executable.decision is ScalpDecision.EXECUTABLE
    assert len(submitted) == 1
    assert engine.get("FAST", "stable").state is WarriorOpportunityState.ORDER_PENDING


def test_confirmation_wait_recovers_without_terminalizing_scalp_generation() -> None:
    calls = []

    def submit(value):
        calls.append(value)
        if len(calls) == 1:
            return CanonicalScalpSubmission(
                False, False, False, False,
                'EXECUTION_QUOTE_UNAVAILABLE', recoverable=True,
            )
        return CanonicalScalpSubmission(
            True, True, True, False, 'AUTHORIZED',
        )

    engine = WarriorOpportunityEngine()
    scalper = QuickScalper(
        engine=engine, ownership=SymbolOwnershipRegistry(),
        config=QuickScalperConfig(enabled=True),
        authorize_and_submit=submit,
    )
    value = snapshot(generation='confirmation-recovery')

    scalper.observe(value)
    waiting = engine.get('FAST', 'confirmation-recovery')
    scalper.observe(value)
    advanced = engine.get('FAST', 'confirmation-recovery')

    assert waiting is not None
    assert waiting.state is WarriorOpportunityState.WAITING_EXECUTION
    assert advanced is not None
    assert advanced.state is WarriorOpportunityState.ORDER_PENDING
    assert len(calls) == 2


def test_executable_quote_freshness_is_hard_five_second_fail_closed() -> None:
    assessed = QuickScalperPolicy().assess(snapshot(age=5.001))
    assert assessed.decision is ScalpDecision.REJECTED_HARD_SAFETY
    assert assessed.reason == "PROVIDER_DATA_STALE"
    assert QuickScalperConfig().provider_freshness_seconds == D("5")


def test_stale_last_does_not_veto_fresh_executable_quote() -> None:
    value = snapshot()
    fresh = NOW - timedelta(seconds=0.2)
    assessed = QuickScalperPolicy().assess(replace(
        value,
        last_timestamp=NOW - timedelta(seconds=30),
        bid_timestamp=fresh,
        ask_timestamp=fresh,
    ))
    assert assessed.decision is ScalpDecision.EXECUTABLE
    assert assessed.reason == "POSITIVE_EXECUTABLE_EDGE"


def test_future_last_timestamp_remains_fail_closed() -> None:
    value = snapshot()
    assessed = QuickScalperPolicy().assess(replace(
        value,
        last_timestamp=NOW + timedelta(seconds=1),
    ))
    assert assessed.decision is ScalpDecision.REJECTED_HARD_SAFETY
    assert assessed.reason == "PROVIDER_DATA_STALE"


def test_canonical_submitter_and_shared_engine_create_one_protected_intent() -> None:
    submitted = []
    engine = WarriorOpportunityEngine()
    scaler = QuickScalper(
        engine=engine, ownership=SymbolOwnershipRegistry(),
        config=QuickScalperConfig(enabled=True),
        authorize_and_submit=lambda value: (
            submitted.append(value) or CanonicalScalpSubmission(
                True, True, True, True, "AUTHORIZED",
            )
        ),
    )
    scaler.observe(snapshot())
    scaler.observe(snapshot())
    item = engine.get("FAST", "g1")
    assert len(submitted) == 1
    assert item is not None and item.state is WarriorOpportunityState.ORDER_PENDING
    assert item.order_intent_result == "SUBMITTED"


def test_obsolete_generation_cannot_authorize() -> None:
    engine = WarriorOpportunityEngine()
    policy = QuickScalperPolicy()
    engine.arm(policy.assess(snapshot(generation="old")).opportunity)
    engine.arm(policy.assess(snapshot(generation="new")).opportunity)
    assert engine.mark_authorization_evaluated(
        "FAST", "old", at=NOW, authorized=True, reason="AUTHORIZED",
    ) is None


def test_strategy_ownership_is_mutually_exclusive() -> None:
    owners = SymbolOwnershipRegistry()
    assert owners.acquire("FAST", StrategyOwner.WARRIOR_MOMENTUM, "w1")
    assert not owners.acquire("FAST", StrategyOwner.QUICK_SCALPER, "s1")
    assert not owners.release("FAST", StrategyOwner.QUICK_SCALPER, "s1")
    assert owners.release("FAST", StrategyOwner.WARRIOR_MOMENTUM, "w1")
    assert owners.acquire("FAST", StrategyOwner.QUICK_SCALPER, "s1")


def test_warrior_ownership_blocks_scalper_with_explicit_authorization_reason() -> None:
    owners = SymbolOwnershipRegistry()
    engine = WarriorOpportunityEngine()
    assert owners.acquire("FAST", StrategyOwner.WARRIOR_MOMENTUM, "warrior")
    scalper = QuickScalper(
        engine=engine, ownership=owners,
        config=QuickScalperConfig(enabled=True),
        authorize_and_submit=lambda _value: CanonicalScalpSubmission(
            True, True, True, False, "AUTHORIZED",
        ),
    )
    scalper.observe(snapshot())
    item = engine.get("FAST", "g1")
    assert item is not None
    assert item.authorization_result == "REJECTED"
    assert item.authorization_reason == "SYMBOL_OWNED_BY_OTHER_STRATEGY"


def test_guard_bounds_duplicates_cooldown_and_losses() -> None:
    config = QuickScalperConfig(enabled=True, maximum_consecutive_losses=1)
    guard = QuickScalperGuard(config)
    assert guard.allow("FAST", "g1", NOW)[0]
    guard.record_attempt("FAST", "g1", NOW)
    assert guard.allow("FAST", "g1", NOW)[1] == "DUPLICATE_GENERATION"
    guard.record_close("FAST", NOW, D("-1"))
    assert guard.allow("FAST", "g2", NOW + timedelta(seconds=121))[1] == "CONSECUTIVE_LOSS_GUARD"


def position(owner: StrategyOwner) -> ProfitRetentionPosition:
    return ProfitRetentionPosition(
        owner, "FAST", "life", NOW, D("5"), D("4.80"), D("4.80"),
        D("5"),
    )


def test_warrior_noise_does_not_tighten_but_meaningful_mfe_does() -> None:
    policy = ProfitRetentionPolicy()
    small = policy.evaluate(position(StrategyOwner.WARRIOR_MOMENTUM),
                            executable_bid=D("5.10"), observed_at=NOW)
    assert small.action is ProfitAction.NONE
    peaked = policy.evaluate(position(StrategyOwner.WARRIOR_MOMENTUM),
                             executable_bid=D("5.40"), observed_at=NOW)
    defended = policy.evaluate(peaked.position, executable_bid=D("5.25"),
                               observed_at=NOW + timedelta(seconds=5))
    assert defended.action is ProfitAction.TIGHTEN_STOP


def test_olox_shape_does_not_infer_profit_when_executable_bid_never_advances() -> None:
    """A LAST/high impression is not executable favorable excursion."""
    value = ProfitRetentionPosition(
        StrategyOwner.WARRIOR_MOMENTUM,
        "OLOX",
        "olox-life",
        NOW,
        D("1.76"),
        D("1.69"),
        D("1.69"),
        D("1.76"),
    )

    decision = ProfitRetentionPolicy().evaluate(
        value,
        executable_bid=D("1.76"),
        observed_at=NOW + timedelta(seconds=2),
    )

    assert decision.action is ProfitAction.NONE
    assert decision.position.peak_executable_bid == D("1.76")
    assert decision.peak_r == D("0")
    assert decision.desired_stop is None


def test_scalp_uses_bid_for_stall_exit_and_max_hold() -> None:
    policy = ProfitRetentionPolicy()
    value = position(StrategyOwner.QUICK_SCALPER)
    no_profit = policy.evaluate(value, executable_bid=D("5.04"),
                                observed_at=NOW, momentum_stalled=True)
    profit = policy.evaluate(value, executable_bid=D("5.06"),
                             observed_at=NOW, momentum_stalled=True)
    expired = policy.evaluate(value, executable_bid=D("4.99"),
                              observed_at=NOW + timedelta(seconds=301))
    assert no_profit.action is ProfitAction.NONE
    assert profit.action is ProfitAction.FULL_EXIT
    assert expired.action is ProfitAction.FULL_EXIT


def test_submitted_unfilled_entry_is_recorded_before_fill_protection() -> None:
    engine = WarriorOpportunityEngine()
    scaler = QuickScalper(
        engine=engine, ownership=SymbolOwnershipRegistry(),
        config=QuickScalperConfig(enabled=True),
        authorize_and_submit=lambda _value: CanonicalScalpSubmission(
            True, True, True, False, "PROTECTION_UNCONFIRMED",
        ),
    )
    scaler.observe(snapshot())
    assert scaler.guard.allow("FAST", "g1", NOW)[1] == "DUPLICATE_GENERATION"

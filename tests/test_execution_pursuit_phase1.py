from datetime import datetime, timezone
from decimal import Decimal

from app.strategies.warrior_momentum.execution_pursuit import (
    ExecutionPursuitDecision,
    assess_top_of_book_pursuit,
)


NOW = datetime(2026, 9, 10, 19, 40, tzinfo=timezone.utc)
D = Decimal


def assess(**overrides):
    values = dict(
        evaluated_at=NOW,
        working_limit=D("7.20"),
        best_bid=D("7.22"),
        best_ask=D("7.23"),
        bid_size=D("1200"),
        ask_size=D("600"),
        spread_percent=D("0.14"),
        maximum_spread_percent=D("1.25"),
        quote_fresh=True,
        liquidity_ok=True,
        thesis_valid=True,
        quality_ok=True,
        replacement_budget_available=True,
        structural_stop=D("6.93"),
        expected_reward=D("0.90"),
    )
    values.update(overrides)
    return assess_top_of_book_pursuit(**values)


def test_advancing_top_of_book_requests_one_bounded_pursuit_level():
    result = assess()
    assert result.decision is ExecutionPursuitDecision.PURSUE_ONE_LEVEL
    assert result.proposed_limit == D("7.23")
    assert result.advancing_pressure is True
    assert result.data_mode == "TOP_OF_BOOK"


def test_wide_spread_blocks_pursuit_without_changing_thesis():
    result = assess(spread_percent=D("1.26"))
    assert result.decision is ExecutionPursuitDecision.BLOCKED
    assert result.reason == "SPREAD_WIDE"


def test_missing_size_does_not_claim_order_flow_pressure():
    result = assess(bid_size=None, ask_size=None)
    assert result.decision is ExecutionPursuitDecision.BLOCKED
    assert result.reason == "ORDER_FLOW_SIZE_UNAVAILABLE"


def test_reversed_or_unbalanced_book_holds_passive():
    result = assess(bid_size=D("400"), ask_size=D("800"))
    assert result.decision is ExecutionPursuitDecision.HOLD_PASSIVE
    assert result.reason == "ADVANCING_PRESSURE_NOT_CONFIRMED"


def test_stale_quality_or_liquidity_blocks_without_market_order():
    assert assess(quote_fresh=False).reason == "STALE_QUOTE"
    assert assess(quality_ok=False).reason == "OPPORTUNITY_QUALITY_BLOCKED"
    assert assess(liquidity_ok=False).reason == "LIQUIDITY_BLOCKED"


def test_invalid_thesis_or_risk_abandons():
    assert assess(thesis_valid=False).decision is ExecutionPursuitDecision.ABANDON
    assert assess(structural_stop=D("7.20")).reason == "STRUCTURAL_STOP_INVALID"
    assert assess(expected_reward=D("0.20")).reason == "REWARD_RISK_UNACCEPTABLE"


def test_replacement_budget_is_bounded():
    result = assess(replacement_budget_available=False)
    assert result.decision is ExecutionPursuitDecision.ABANDON
    assert result.reason == "PURSUIT_BUDGET_EXHAUSTED"


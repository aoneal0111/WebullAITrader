"""Fixed-1R outcomes are hypothetical and must not manufacture trade wins."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.strategies.warrior_momentum.models import MinuteBar
from app.strategy_research.warrior_micro_outcome_screen import outcome


BASE = datetime(2026, 7, 17, 13, 30, tzinfo=UTC)


def bar(index, open_, high, low, close=None, *, stamp=None):
    return MinuteBar(
        "TEST", BASE + timedelta(minutes=index) if stamp is None else stamp,
        D(str(open_)), D(str(high)), D(str(low)),
        D(str(open_ if close is None else close)), D("1000"),
    )


def test_hypothetical_next_open_target_with_costs():
    bars = (
        bar(0, "10", "10.2", "9.8"),
        bar(1, "10", "11.1", "9.9"),
    )
    result = outcome(bars, 0, structural_stop=D("9"), cost_per_side_per_share=D("0.05"))
    assert result["status"] == "TARGET_FIRST_OBSERVED"
    assert result["hypothetical_net_r"] == "0.90"
    assert result["bars_until_observation"] == 1


def test_hypothetical_stop_not_a_winner():
    bars = (
        bar(0, "10", "10", "9.5"),
        bar(1, "10", "10.4", "8.9"),
    )
    result = outcome(bars, 0, structural_stop=D("9"))
    assert result["status"] == "STOP_FIRST_OBSERVED"
    assert D(result["hypothetical_net_r"]) == -1


def test_ambiguous_same_bar_is_not_assumed_profitable():
    bars = (
        bar(0, "10", "10", "9.5"),
        bar(1, "10", "11.1", "8.9"),
    )
    result = outcome(bars, 0, structural_stop=D("9"))
    assert result["status"] == "AMBIGUOUS_SAME_BAR"
    assert result["hypothetical_net_r"] is None


def test_no_future_or_nonconsecutive_bar_cannot_be_assumed_filled():
    bars = (bar(0, "10", "10", "9.5"),)
    assert outcome(bars, 0, structural_stop=D("9"))["status"] == "NO_CONSECUTIVE_NEXT_OPEN"
    gaps = (
        bar(0, "10", "10", "9.5"),
        bar(2, "10", "11.5", "9.9"),
    )
    assert outcome(gaps, 0, structural_stop=D("9"))["status"] == "NO_CONSECUTIVE_NEXT_OPEN"


def test_gap_inside_horizon_is_censored():
    bars = (
        bar(0, "10", "10", "9.5"),
        bar(1, "10", "10.4", "9.6"),
        bar(3, "10.3", "11.5", "9.9"),
    )
    assert outcome(bars, 0, structural_stop=D("9"))["status"] == "GAP_CENSORED"


def test_invalid_stop_cost_horizon_fail_closed():
    bars = (bar(0, "10", "10", "9.5"), bar(1, "10", "10.2", "9.9"))
    with pytest.raises(ValueError, match="INVALID_COST"):
        outcome(bars, 0, structural_stop=D("9"), cost_per_side_per_share=D("-0.1"))
    with pytest.raises(ValueError, match="INVALID_STOP"):
        outcome(bars, 0, structural_stop=D("NaN"))
    with pytest.raises(ValueError, match="INVALID_HORIZON"):
        outcome(bars, 0, structural_stop=D("9"), horizon_bars=0)


def test_invalid_risk_is_excluded():
    bars = (bar(0, "10", "10", "9.5"), bar(1, "9", "9.4", "8.8"))
    assert outcome(bars, 0, structural_stop=D("9"))["status"] == "INVALID_NEXT_OPEN_RISK"

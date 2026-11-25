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


def test_end_to_end_normalized_partition_requires_provenance_and_future_bar(tmp_path):
    import json
    from dataclasses import asdict
    from app.strategy_research.warrior_micro_selection_history import run
    from app.strategy_research.warrior_micro_outcome_screen import study
    from tests.warrior_momentum.test_strategy import micro_bars

    normalized = tmp_path / "normalized"
    reports = tmp_path / "reports"
    normalized.mkdir()
    reports.mkdir()
    source = normalized / "XYZ_2026-08-10.jsonl"
    with source.open("x", encoding="utf-8") as stream:
        for bar in micro_bars():
            data = asdict(bar)
            data["timestamp"] = bar.timestamp.isoformat()
            for key in ("open", "high", "low", "close", "volume"):
                data[key] = str(data[key])
            stream.write(json.dumps(data) + "\n")
    prior = source.read_bytes()
    run(source, symbol="XYZ", day="2026-08-10",
        output=reports / "XYZ_2026-08-10-timing.json")
    result = study(reports, normalized)
    assert result["source_report_count"] == 1
    assert result["observed_trigger_runs"] == 1
    assert result["outcomes"] == {"NO_CONSECUTIVE_NEXT_OPEN": 1}
    assert result["resolved_barrier_observations"] == 0
    assert result["orders"] == result["network_requests"] == 0
    assert source.read_bytes() == prior

    source.write_bytes(prior.replace(b'"symbol": "XYZ"', b'"symbol":"XYZ"', 1))
    with pytest.raises(ValueError, match="SOURCE_SHA256_MISMATCH"):
        study(reports, normalized)

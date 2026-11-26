"""Source-reconciled cost analysis must not promote hypothetical results."""
from decimal import Decimal as D

import pytest

from app.strategy_research.warrior_micro_cost_sensitivity import analyze


def fixture():
    return {
        "scope": "RESEARCH_ONLY_WARRIOR_MICRO_NEXT_OPEN_BARRIER_V1",
        "orders": 0,
        "network_requests": 0,
        "production_policy_changed": False,
        "cost_per_side_per_share": "0.01",
        "observed_trigger_runs": 3,
        "resolved_barrier_observations": 2,
        "hypothetical_net_r_sum_resolved_only": "-0.06",
        "outcomes": {"TARGET_FIRST_OBSERVED": 1,
                     "STOP_FIRST_OBSERVED": 1,
                     "AMBIGUOUS_SAME_BAR": 1},
        "cases": [
            {"symbol": "XYZ", "day": "2026-07-01", "signal_close_at": "a",
             "status": "TARGET_FIRST_OBSERVED", "hypothetical_net_r": "0.96",
             "entry_open": "10", "structural_stop": "9.5", "target": "10.5"},
            {"symbol": "XYZ", "day": "2026-07-02", "signal_close_at": "b",
             "status": "STOP_FIRST_OBSERVED", "hypothetical_net_r": "-1.02",
             "entry_open": "10", "structural_stop": "9", "target": "11"},
            {"symbol": "XYZ", "day": "2026-07-02", "signal_close_at": "c",
             "status": "AMBIGUOUS_SAME_BAR", "hypothetical_net_r": None},
        ],
    }


def test_cost_scenarios_and_reconciled_original_total():
    result = analyze(fixture(), costs=("0", "0.005", "0.01"))
    assert result["observed_trigger_runs"] == 3
    assert result["resolved_cases"] == 2
    assert result["unresolved_cases"] == 1
    assert result["resolved_only_win_fraction"] == "0.5"
    assert D(result["baseline_reconciled_net_r"]) == D("-0.06")
    assert [D(x["resolved_only_net_r"]) for x in result["scenarios"]] == [
        D("0"), D("-0.03"), D("-0.06")]
    assert result["daily_group_counts"][0]["resolved_cases"] == 1
    assert result["production_policy_changed"] is False
    assert result["orders"] == 0


def test_positive_break_even_cost_geometry_is_exact():
    data = fixture()
    data["cases"].pop(1)
    data["cases"].pop(1)
    data["observed_trigger_runs"] = 1
    data["resolved_barrier_observations"] = 1
    data["hypothetical_net_r_sum_resolved_only"] = "0.96"
    data["outcomes"] = {"TARGET_FIRST_OBSERVED": 1}
    result = analyze(data, costs=("0", "0.25", "0.3"))
    assert D(result["assumed_cost_per_side_share_break_even_r"]) == D("0.25")
    assert result["scenarios"][1]["profitable_resolved_only"] is False
    assert result["scenarios"][2]["profitable_resolved_only"] is False


@pytest.mark.parametrize("field,value,error", [
    ("hypothetical_net_r_sum_resolved_only", "100", "SOURCE_TOTAL"),
    ("observed_trigger_runs", 4, "SOURCE_CASE_COUNT"),
    ("resolved_barrier_observations", 3, "SOURCE_CASE_COUNT"),
])
def test_source_integrity_refuses_inconsistent_totals(field, value, error):
    data = fixture()
    data[field] = value
    with pytest.raises(ValueError, match=error):
        analyze(data)


def test_ambiguous_same_bar_cannot_be_counted_as_win():
    data = fixture()
    data["cases"][-1]["hypothetical_net_r"] = "1"
    with pytest.raises(ValueError, match="UNRESOLVED_CASE_HAS_NET_R"):
        analyze(data)


def test_duplicate_cases_and_changed_target_cannot_pass():
    data = fixture()
    data["cases"][1]["signal_close_at"] = "a"
    data["cases"][1]["day"] = "2026-07-01"
    with pytest.raises(ValueError, match="DUPLICATE_OR_UNIDENTIFIED_CASE"):
        analyze(data)
    data = fixture()
    data["cases"][0]["target"] = "10.6"
    with pytest.raises(ValueError, match="INVALID_1R_BRACKET_GEOMETRY"):
        analyze(data)


def test_invalid_costs_fail_closed():
    for costs in (("-0.1",), ("NaN",), ("0.01", "0.01")):
        with pytest.raises(ValueError):
            analyze(fixture(), costs=costs)


def test_rejects_unknown_scope():
    data = fixture()
    data["scope"] = "LIVE_TRADES"
    with pytest.raises(ValueError, match="EXPECTED_HYPOTHETICAL"):
        analyze(data)

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.trade_intelligence.knowledge.analysis import (capital_scenarios, cohort_report, chronological_splits,
                                                        constant_risk_scenarios, entry_delay_research, hold_vs_reentry,
                                                        runner_path_analysis, simulate_profit_policy, transition_matrix,
                                                        transition_record, walk_forward_folds)
from app.trade_intelligence.knowledge.features import feature_snapshot
from app.trade_intelligence.knowledge.models import HistoricalBar


def bars(day=date(2026, 8, 7)):
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=14, minute=30)
    return tuple(HistoricalBar("ABC", start + timedelta(minutes=i), Decimal(10 + i / 10),
                               Decimal(10.2 + i / 10), Decimal(9.9 + i / 10), Decimal(10.1 + i / 10),
                               Decimal(100 + i), "REGULAR", "ALPACA", "UTC", 1)
                 for i in range(12))


def row(day, episode="e1", strategy="FIRST_PULLBACK"):
    snapshot = feature_snapshot(bars(day), datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(hour=14, minute=42),
                                previous_close=Decimal("9"), trigger_price=Decimal("10.9"), structural_stop=Decimal("10"),
                                memberships=(strategy,), provider="ALPACA", feed="IEX", repository_commit="abc",
                                normalization_version=1, coverage_class="SINGLE_EXCHANGE_FREE_RESEARCH")
    return {"episode_id": episode, "symbol": "ABC", "trading_date": day.isoformat(), "primary_strategy": strategy,
            "strategy_memberships": (strategy,), "price_at_detection": "11", "gap_percent": "10",
            "features": snapshot, "provenance": {"provider": "ALPACA", "feed": "IEX"},
            "outcomes": {"mfe_percent": "8", "mae_percent": "-2", "maximum_R": "3",
                          "percent_targets": {str(p): {"hit": p <= 5, "first_plan_event": "TARGET_FIRST" if p <= 5 else "STOP_FIRST",
                                                         "elapsed_seconds": p * 60} for p in (2, 3, 5, 8, 10)}}}


def test_snapshot_is_versioned_point_in_time_and_excludes_outcomes():
    snapshot = row(date(2026, 8, 7))["features"]
    assert snapshot["schema_version"] == 1
    assert snapshot["provenance"]["feed"] == "IEX"
    assert snapshot["as_of_timestamp"] <= "2026-08-07T14:42:00+00:00"
    assert "mfe_percent" not in snapshot["values"]
    assert "outcomes" not in snapshot["values"]


def test_cohorts_have_metrics_sample_guard_and_concentration():
    result = cohort_report([row(date(2026, 8, 7)), row(date(2026, 8, 8), "e2")], ("strategy", "time_of_day_bucket"))
    assert result[0]["sample_count"] == 2
    assert result[0]["confidence_state"] == "INSUFFICIENT_SAMPLE"
    assert result[0]["target_hit_rates"]["5"] == 1.0
    assert "SYMBOL_CONCENTRATED" in result[0]["concentration_flags"]


def test_chronological_and_walk_forward_splits_do_not_shuffle():
    rows = [row(date(2026, 8, 7), "a"), row(date(2026, 8, 8), "b"), row(date(2026, 8, 9), "c"), row(date(2026, 8, 10), "d")]
    splits = chronological_splits(rows, train_end="2026-08-08", validation_end="2026-08-09")
    assert [item["episode_id"] for item in splits["TRAIN"]] == ["a", "b"]
    assert [item["episode_id"] for item in splits["VALIDATION"]] == ["c"]
    assert [item["episode_id"] for item in splits["TEST"]] == ["d"]
    folds = walk_forward_folds(rows, train_dates=2, validation_dates=1, test_dates=1)
    assert folds[0]["train_start"] == "2026-08-07"
    assert folds[0]["test_end"] == "2026-08-10"


def test_subminute_delay_is_explicitly_unavailable():
    assert entry_delay_research(row(date(2026, 8, 7)))["sub_minute"] == "UNAVAILABLE_AT_1MIN_RESOLUTION"


def test_profit_policy_is_simulated_and_supports_partial_runner():
    source = row(date(2026, 8, 7))
    result = simulate_profit_policy(source, target_percent=5, partial_percent=50, runner_terminal="SESSION_CLOSE")
    assert result["simulation"] == "SIMULATED"
    assert result["fill_status"] == "NOT_ACTUAL_FILL"
    assert result["total_simulated_return"] == 2.5
    assert runner_path_analysis(source, 5)["hit"] is True
    source["outcomes"]["percent_targets"]["5"]["first_plan_event"] = "INTRABAR_ORDER_UNKNOWN"
    assert simulate_profit_policy(source, target_percent=5, partial_percent=50)["ambiguity_state"] == "INTRABAR_ORDER_UNKNOWN"


def test_capital_and_constant_risk_are_separate_research_scenarios():
    assert capital_scenarios(8, (1000,))["1000"] == 80
    assert constant_risk_scenarios(10, 9, (100,))["100"]["shares"] == 100


def test_transition_matrix_rejects_same_anchor_and_preserves_child_payload_reference():
    parent = row(date(2026, 8, 7), "parent")
    parent.update({"detected_timestamp": "2026-08-07T14:42:00+00:00", "structural_anchor": "anchor-a",
                   "trigger_price": "10", "structural_stop": "9"})
    child = row(date(2026, 8, 8), "child", "HIGHER_LOW_CONTINUATION")
    child.update({"detected_timestamp": "2026-08-08T14:42:00+00:00", "structural_anchor": "anchor-b",
                  "trigger_price": "11", "structural_stop": "10"})
    transition = transition_record(parent, child)
    assert transition["research_version"]
    assert transition["child_episode_id"] == "child"
    assert transition_matrix([transition])[0]["sample_count"] == 1
    assert hold_vs_reentry(parent, child)["simulated"] is True
    child["structural_anchor"] = "anchor-a"
    try:
        transition_record(parent, child)
    except ValueError as error:
        assert str(error) == "SAME_STRUCTURAL_ANCHOR_NOT_REENTRY"
    else:
        raise AssertionError("same anchor accepted as re-entry")

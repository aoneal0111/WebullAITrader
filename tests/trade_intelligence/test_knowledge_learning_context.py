from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.trade_intelligence.knowledge.analysis import (capital_scenarios, cohort_report, chronological_splits,
                                                        constant_risk_scenarios, entry_delay_research, hold_vs_reentry,
                                                        runner_path_analysis, simulate_profit_policy, transition_matrix,
                                                        transition_record, walk_forward_folds, streaming_cohort_report,
                                                        first_tranche_report_streaming, full_research_report_streaming,
                                                        ReportProgress)
import io
from app.trade_intelligence.knowledge.features import feature_snapshot
from app.trade_intelligence.knowledge.models import HistoricalBar
from app.trade_intelligence.knowledge.storage import KnowledgeStore
from app.trade_intelligence.knowledge.__main__ import main


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


def test_streaming_cohort_matches_legacy_fixture_without_materializing_rows():
    source = [row(date(2026, 8, 7)), row(date(2026, 8, 8), "e2")]
    legacy = cohort_report(source, ("strategy", "time_of_day_bucket"))
    streamed = streaming_cohort_report((item for item in source), ("strategy", "time_of_day_bucket"))
    assert streamed == legacy


def test_streaming_first_tranche_uses_reiterable_source_and_handles_empty():
    source = [row(date(2026, 8, 7)), row(date(2026, 8, 8), "e2")]
    result = first_tranche_report_streaming(lambda: (item for item in source))
    assert result["preset"] == "FIRST_TRANCHE"
    assert result["by_strategy"][0]["sample_count"] == 2
    empty = first_tranche_report_streaming(lambda: iter(()))
    assert empty["by_strategy"] == []


def test_streaming_progress_is_bounded_stderr_with_monotonic_phase_counts():
    source = [row(date(2026, 8, 7), f"e{i}") for i in range(25)]
    stderr = io.StringIO()
    progress = ReportProgress(total=len(source), interval=10, stream=stderr)
    result = first_tranche_report_streaming(lambda: (item for item in source), total=len(source), progress=progress)
    lines = [line for line in stderr.getvalue().splitlines() if line.startswith("ATLAS_REPORT_PROGRESS")]
    assert result["by_strategy"][0]["sample_count"] == 25
    assert lines and len(lines) < 40
    assert any("phase=BY_STRATEGY" in line for line in lines)
    assert any("phase=TIME_OF_DAY" in line for line in lines)
    phase_counts = {}
    for line in lines:
        fields = dict(item.split("=", 1) for item in line.split()[1:])
        phase_counts.setdefault(fields["phase"], []).append(int(fields["records_processed"]))
    assert all(values == sorted(values) for values in phase_counts.values())
    assert all("records_total=25" in line and "percent_complete=" in line for line in lines)


def test_streaming_progress_does_not_change_metrics_or_stdout(monkeypatch):
    source = [row(date(2026, 8, 7), "e1"), row(date(2026, 8, 8), "e2")]
    without = first_tranche_report_streaming(lambda: (item for item in source))
    stderr = io.StringIO()
    with_progress = first_tranche_report_streaming(lambda: (item for item in source), total=2,
                                                   progress=ReportProgress(total=2, interval=100, stream=stderr))
    assert with_progress == without
    assert stderr.getvalue()


def test_cli_keeps_progress_off_stdout(tmp_path, capsys):
    output = tmp_path / "output"
    KnowledgeStore(output / "corpus")
    report_file = tmp_path / "reports" / "first.json"
    assert main(["report", "--output", str(output), "--preset", "first-tranche", "--report-file", str(report_file)]) == 0
    captured = capsys.readouterr()
    assert "ATLAS_REPORT_PROGRESS" not in captured.out
    assert "report_file" in captured.out
    assert "ATLAS_REPORT_PROGRESS" in captured.err


def test_report_file_is_atomic_and_empty_corpus_is_supported(tmp_path):
    output = tmp_path / "output"
    KnowledgeStore(output / "corpus")
    report_file = tmp_path / "reports" / "first.json"
    assert main(["report", "--output", str(output), "--preset", "first-tranche", "--report-file", str(report_file)]) == 0
    assert report_file.stat().st_size > 0
    assert report_file.read_text(encoding="utf-8").lstrip().startswith("{")


def test_interrupted_report_does_not_replace_existing_artifact(tmp_path, monkeypatch):
    output = tmp_path / "output"
    KnowledgeStore(output / "corpus")
    report_file = tmp_path / "reports" / "first.json"
    report_file.parent.mkdir()
    report_file.write_text("valid-artifact\n", encoding="utf-8")
    def fail(_factory, **_kwargs):
        raise RuntimeError("REPORT_INTERRUPTED")
    monkeypatch.setattr("app.trade_intelligence.knowledge.__main__.first_tranche_report_streaming", fail)
    try:
        main(["report", "--output", str(output), "--preset", "first-tranche", "--report-file", str(report_file)])
    except RuntimeError as error:
        assert str(error) == "REPORT_INTERRUPTED"
    else:
        raise AssertionError("interrupted report unexpectedly succeeded")
    assert report_file.read_text(encoding="utf-8") == "valid-artifact\n"


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


def test_full_research_is_disk_backed_and_reports_phase_one_capabilities():
    source = [row(date(2026, 8, 7), "e1"), row(date(2026, 8, 8), "e2")]
    stderr = io.StringIO()
    result = full_research_report_streaming(lambda: iter(source), total=2,
                                             progress=ReportProgress(total=2, interval=1, stream=stderr))
    assert result["preset"] == "FULL_RESEARCH"
    assert result["metadata"]["no_random_split"] is True
    assert result["capabilities"]["profit_research"] == "IMPLEMENTED"
    assert result["context_analysis"]["gap"]["status"] == "UNAVAILABLE_FROM_PERSISTED_EPISODE_FIELD"
    assert result["context_analysis"]["pullback"]["status"] == "UNAVAILABLE_NULL_DOMINATED"
    assert result["context_analysis"]["volume"]["source_fields"]
    assert "phase=INGEST" in stderr.getvalue()
    assert "phase=STRATEGY_SCORECARDS" in stderr.getvalue()
    assert "ATLAS_REPORT_PROGRESS" not in str(result)


def test_full_research_empty_corpus_has_safe_lifecycle():
    result = full_research_report_streaming(lambda: iter(()))
    assert result["metadata"]["records_ingested"] == 0
    assert result["walk_forward"]["number_of_folds"] == 0
    assert all(item["sample_count"] == 0 for item in result["strategy_scorecards"].values())


def test_full_research_temporal_boundaries_are_strict_and_ordered():
    source = [row(date(2026, 8, 1) + timedelta(days=i), f"e{i}") for i in range(10)]
    result = full_research_report_streaming(lambda: iter(source))
    temporal = result["temporal"]
    assert temporal["method"] == "strict trading-date chronology"
    assert temporal["random_split"] is False
    assert temporal["train_end"] < temporal["validation_end"] < temporal["test_start"]
    assert temporal["splits"]["TRAIN"]["FIRST_PULLBACK"]["sample_count"] == 6
    assert temporal["splits"]["VALIDATION"]["FIRST_PULLBACK"]["sample_count"] == 2
    assert temporal["splits"]["TEST"]["FIRST_PULLBACK"]["sample_count"] == 2


def test_full_research_phase_two_matches_row_policy_semantics():
    source = row(date(2026, 8, 7))
    source["trigger_price"] = "11"
    source["structural_stop"] = "10"
    source["outcomes"]["horizons"] = {"3600": {"mfe_percent": "12"}}
    source["outcomes"]["r_targets"] = {
        str(target): {"hit": target <= 2, "first_plan_event": "TARGET_FIRST", "elapsed_seconds": target * 100}
        for target in (0.5, 1, 1.5, 2, 3, 4, 5)
    }
    result = full_research_report_streaming(lambda: iter((source,)))
    strategy = result["profit_research"]["FIRST_PULLBACK"]
    expected = simulate_profit_policy(source, target_percent=8, partial_percent=50)
    actual = strategy["partial_exit_policies"]["8"]["50"]["gross_return_percent_mean"]
    assert actual == expected["total_simulated_return"]
    assert result["r_multiple_research"]["FIRST_PULLBACK"]["2"]["hit_rate"] == 1.0
    assert result["constant_risk_scenarios"]["FIRST_PULLBACK"]["100"]["valid_observations"] == 1
    assert result["capabilities"]["reentry"] == "NOT_YET_IMPLEMENTED"

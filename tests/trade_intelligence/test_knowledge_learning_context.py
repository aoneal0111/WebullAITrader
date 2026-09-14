from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
import sqlite3

from app.trade_intelligence.knowledge.analysis import (capital_scenarios, cohort_report, chronological_splits,
                                                        constant_risk_scenarios, entry_delay_research, hold_vs_reentry,
                                                        runner_path_analysis, simulate_profit_policy, transition_matrix,
                                                        transition_record, walk_forward_folds, streaming_cohort_report,
                                                        first_tranche_report_streaming, full_research_report_streaming,
                                                        ReportProgress, _agreement, _benchmark_state,
                                                        _composite_regime, _vwap_state, _volatility_state,
                                                        _execution_context, execution_entry_reachability,
                                                        participation_capacity, target_realism_models,
                                                        execution_adjusted_r)
import io
import hashlib
from app.trade_intelligence.knowledge.features import feature_snapshot
from app.trade_intelligence.knowledge.models import HistoricalBar
from app.trade_intelligence.knowledge.storage import KnowledgeStore
from app.trade_intelligence.knowledge.__main__ import main
from app.trade_intelligence.knowledge.benchmark import BENCHMARK_REGIME_VERSION, build_benchmark_context


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
    assert result["capabilities"]["reentry"] == "IMPLEMENTED"
    assert result["capabilities"]["failure_analysis"] == "IMPLEMENTED"


def test_full_research_phase_three_joins_reentry_and_reports_evidence_failures():
    parent = row(date(2026, 8, 7), "parent", "FIRST_PULLBACK")
    child = row(date(2026, 8, 7), "child", "HOD_BREAKOUT")
    parent.update(detected_timestamp="2026-08-07T14:42:00+00:00", structural_anchor="a")
    child.update(detected_timestamp="2026-08-07T14:50:00+00:00", structural_anchor="b")
    child["reentry"] = {"parent_episode_id": "parent", "time_since_parent": 480,
                         "price_change_since_parent": "1.5", "pullback_from_parent_MFE": "2"}
    result = full_research_report_streaming(lambda: iter((parent, child)))
    reentry = result["reentry"]
    assert reentry["version"] == "ATLAS_REENTRY_TRANSITIONS_V1"
    assert reentry["valid_transitions"] == 1
    assert reentry["transition_matrix"][0]["sample_count"] == 1
    assert reentry["partial_hold_plus_reentry"]["50"]["simulation"] == "SIMULATED"
    assert "TARGET_8_NOT_REACHED" in result["failure_analysis"]["classes"]


def test_full_research_phase_three_rejects_orphan_and_same_anchor_links():
    orphan = row(date(2026, 8, 7), "orphan", "HOD_BREAKOUT")
    orphan["reentry"] = {"parent_episode_id": "missing"}
    same = row(date(2026, 8, 7), "same", "HOD_BREAKOUT")
    same.update(detected_timestamp="2026-08-07T14:50:00+00:00", structural_anchor="same-anchor")
    same["reentry"] = {"parent_episode_id": "same", "time_since_parent": 1}
    result = full_research_report_streaming(lambda: iter((orphan, same)))
    assert result["reentry"]["orphaned_transitions"] == 1
    assert result["reentry"]["valid_transitions"] == 0
    assert result["reentry"]["invalid_or_self_transitions"] >= 1


def test_phase_four_b_gap_join_is_date_keyed_and_context_is_point_in_time(tmp_path):
    source = row(date(2026, 8, 7))
    daily = tmp_path / "candidate_days.jsonl"
    daily.write_text(json.dumps({"symbol": "ABC", "trading_date": "2026-08-07", "previous_close": "9", "open": "10", "gap_percent": "11.11"}) + "\n" +
                     json.dumps({"symbol": "ABC", "trading_date": "2026-08-08", "previous_close": "100", "open": "101", "gap_percent": "1"}) + "\n", encoding="utf-8")
    result = full_research_report_streaming(lambda: iter((source,)), daily_context_path=daily)
    gap = result["context_analysis"]["gap_context"]
    assert gap["status"] == "AVAILABLE_FROM_CANDIDATE_DAY_ARTIFACT"
    assert gap["available_count"] == 1
    assert {item["group"] for item in gap["buckets"]} == {"LARGE_10_20"}


def test_phase_four_b_contexts_are_explicit_and_future_bars_are_not_used():
    source = row(date(2026, 8, 7))
    original = full_research_report_streaming(lambda: iter((source,)))
    source["detected_timestamp"] = "2026-08-07T14:42:00+00:00"
    source["bar_window"] = [{"timestamp": "2026-08-07T14:43:00+00:00", "high": "999", "low": "999", "close": "999", "open": "999", "volume": "1"}]
    changed = full_research_report_streaming(lambda: iter((source,)))
    assert original["context_analysis"]["generic_pullback"]["coverage_percent"] == changed["context_analysis"]["generic_pullback"]["coverage_percent"]
    assert original["context_analysis"]["gap_context"]["status"] == "UNAVAILABLE_FROM_PERSISTED_EPISODE_FIELD"
    assert original["context_analysis"]["premarket"]["missing_count"] == 0


def test_benchmark_context_is_point_in_time_and_side_table_is_versioned(tmp_path):
    root = tmp_path / "benchmark"
    (root / "normalized").mkdir(parents=True)
    rows = []
    start = "2026-08-07T13:30:00+00:00"
    for index, price in enumerate((100, 101, 102, 103, 104, 105, 106)):
        rows.append({"symbol": "SPY", "timestamp": (datetime.fromisoformat(start) + timedelta(minutes=index)).isoformat(),
                     "session": "REGULAR", "open": str(price), "high": str(price + 1), "low": str(price - 1),
                     "close": str(price), "volume": "100", "provider": "ALPACA", "feed": "IEX"})
    (root / "normalized" / "SPY_2026-08-07.jsonl").write_text("\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8")
    database = build_benchmark_context(root, start=date(2026, 8, 7), end=date(2026, 8, 7))
    connection = sqlite3.connect(database)
    values = connection.execute("SELECT return_from_open, derivation_version FROM benchmark_context WHERE benchmark_symbol='SPY' ORDER BY effective_timestamp").fetchall()
    connection.close()
    assert values[0][0] == 0
    assert abs(values[-1][0] - 6) < 1e-9
    assert all(value[1] == BENCHMARK_REGIME_VERSION for value in values)


def test_full_research_benchmark_asof_join_excludes_future_bars(tmp_path):
    source = row(date(2026, 8, 7))
    source["detected_timestamp"] = "2026-08-07T13:34:00+00:00"
    database = tmp_path / "benchmark.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE benchmark_context (benchmark_symbol TEXT, trading_date TEXT, effective_timestamp TEXT, session TEXT, price REAL, return_from_open REAL, return_1m REAL, return_5m REAL, return_10m REAL, return_30m REAL, vwap REAL, above_vwap INTEGER, hod_distance REAL, lod_distance REAL, range_percent REAL, volatility REAL, momentum REAL, volume_acceleration REAL, opening_range_position TEXT, derivation_version TEXT)")
    values = [(symbol, "2026-08-07", timestamp, "REGULAR", price, ret, ret, ret, ret, ret, price, 1, 0, 0, 1, 1, ret, 1, "INSIDE", "ATLAS_BENCHMARK_REGIME_V1") for symbol, timestamp, price, ret in (("SPY", "2026-08-07T13:34:00+00:00", 101, 1), ("SPY", "2026-08-07T13:35:00+00:00", 999, 999))]
    connection.executemany("INSERT INTO benchmark_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
    connection.commit(); connection.close()
    digest_before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = full_research_report_streaming(lambda: iter((source,)), benchmark_context_path=database)
    repeated = full_research_report_streaming(lambda: iter((source,)), benchmark_context_path=database)
    assert result["context_analysis"]["benchmark_context"]["available_count"] == 0
    assert result["context_analysis"]["benchmark_context"]["status"] == "INSUFFICIENT_DATA"
    assert repeated["context_analysis"]["benchmark_context"] == result["context_analysis"]["benchmark_context"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == digest_before


def test_benchmark_context_is_reused_once_per_episode_with_multiple_memberships(tmp_path):
    source = row(date(2026, 8, 7))
    source["detected_timestamp"] = "2026-08-07T13:34:00+00:00"
    source["strategy_memberships"] = ("FIRST_PULLBACK", "HOD_BREAKOUT")
    database = tmp_path / "benchmark.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE benchmark_context (benchmark_symbol TEXT, trading_date TEXT, effective_timestamp TEXT, session TEXT, price REAL, return_from_open REAL, return_1m REAL, return_5m REAL, return_10m REAL, return_30m REAL, vwap REAL, above_vwap INTEGER, hod_distance REAL, lod_distance REAL, range_percent REAL, volatility REAL, momentum REAL, volume_acceleration REAL, opening_range_position TEXT, derivation_version TEXT)")
    values = [(symbol, "2026-08-07", "2026-08-07T13:34:00+00:00", "REGULAR", 101, 1, 1, 1, 1, 1, 101, 1, 0, 0, 1, 1, 1, 1, "INSIDE", "ATLAS_BENCHMARK_REGIME_V1") for symbol in ("SPY", "QQQ", "IWM")]
    connection.executemany("INSERT INTO benchmark_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
    connection.commit(); connection.close()
    result = full_research_report_streaming(lambda: iter((source,)), benchmark_context_path=database)
    assert result["context_analysis"]["benchmark_context"]["available_count"] == 1
    assert result["context_analysis"]["benchmark_context"]["coverage_percent"] == 100


def test_benchmark_vwap_agreement_states_are_explicit_and_missing_is_not_neutral():
    agreement = lambda states: _agreement(
        states, positive="ABOVE_VWAP", negative="BELOW_VWAP",
        all_positive="ALL_ABOVE_VWAP", majority_positive="MAJORITY_ABOVE_VWAP",
        mixed="MIXED_VWAP", majority_negative="MAJORITY_BELOW_VWAP",
        all_negative="ALL_BELOW_VWAP")
    assert agreement(("ABOVE_VWAP", "ABOVE_VWAP", "ABOVE_VWAP")) == "ALL_ABOVE_VWAP"
    assert agreement(("ABOVE_VWAP", "ABOVE_VWAP", "BELOW_VWAP")) == "MAJORITY_ABOVE_VWAP"
    assert agreement(("ABOVE_VWAP", "BELOW_VWAP", "AT_VWAP_OR_NEUTRAL")) == "MIXED_VWAP"
    assert agreement(("BELOW_VWAP", "BELOW_VWAP", "BELOW_VWAP")) == "ALL_BELOW_VWAP"
    assert agreement(("ABOVE_VWAP", "MISSING", "BELOW_VWAP")) == "INSUFFICIENT_BENCHMARK_DATA"
    assert _vwap_state(10, 10, 1) == "AT_VWAP_OR_NEUTRAL"


def test_benchmark_momentum_volatility_and_composite_are_deterministic():
    momentum = lambda states: _agreement(
        states, positive="POSITIVE", negative="NEGATIVE",
        all_positive="ALL_POSITIVE", majority_positive="MAJORITY_POSITIVE",
        mixed="MIXED", majority_negative="MAJORITY_NEGATIVE",
        all_negative="ALL_NEGATIVE")
    assert momentum(("POSITIVE", "POSITIVE", "NEGATIVE")) == "MAJORITY_POSITIVE"
    assert momentum(("NEGATIVE", "NEGATIVE", "NEGATIVE")) == "ALL_NEGATIVE"
    assert momentum(("POSITIVE", "MISSING", "NEGATIVE")) == "INSUFFICIENT_BENCHMARK_DATA"
    assert _benchmark_state(1, positive="POSITIVE", negative="NEGATIVE") == "POSITIVE"
    assert _benchmark_state(-1, positive="POSITIVE", negative="NEGATIVE") == "NEGATIVE"
    assert _benchmark_state(0, positive="POSITIVE", negative="NEGATIVE") == "NEUTRAL"
    assert _volatility_state(None) == "MISSING"
    assert _volatility_state(.1) == "LOW"
    assert _volatility_state(.5) == "NORMAL"
    assert _volatility_state(1.0) == "HIGH"
    assert _composite_regime("RISK_ON", "ALL_ABOVE_VWAP", "ALL_POSITIVE") == "BROAD_STRENGTH"
    assert _composite_regime("RISK_OFF", "ALL_BELOW_VWAP", "ALL_NEGATIVE") == "BROAD_WEAKNESS"
    assert _composite_regime("RISK_ON", "INSUFFICIENT_BENCHMARK_DATA", "ALL_POSITIVE") == "INSUFFICIENT_BENCHMARK_DATA"


def test_phase_five_execution_context_is_decision_time_and_geometry_aware():
    source = row(date(2026, 8, 7))
    source["trigger_price"] = "10"
    source["structural_stop"] = "9"
    source["features"]["values"]["extension"]["price_at_detection"] = "10.10"
    context = _execution_context(source)
    assert context[0] == "TRIGGER_ALREADY_PASSED_AT_DECISION"
    assert context[1] == "SLIGHTLY_EXTENDED"
    assert round(context[2], 2) == 1.0
    assert round(context[3], 6) == .1
    assert context[4] == 111
    source["features"]["values"]["volume"].pop("rolling_10_mean", None)
    assert _execution_context(source)[5] is None


def test_phase_five_report_labels_bar_proxy_and_unavailable_quote_realism():
    result = full_research_report_streaming(lambda: iter((row(date(2026, 8, 7)),)))
    execution = result["execution_research"]
    assert execution["version"] == "ATLAS_BAR_EXECUTION_RESEARCH_V1"
    assert execution["strategy"]["FIRST_PULLBACK"]["overall"]["execution_model"] == "BAR_BASED"
    assert "NO_SPREAD_CLAIM" in execution["labels"]
    assert execution["target_realism"]["execution_adjusted_outcomes"] == "HYPOTHETICAL_BAR_BASED"


def test_phase_five_entry_reachability_states_are_conservative():
    assert execution_entry_reachability(decision_price=10, trigger_price=11, target_reached=True) == "TRIGGER_REACHED"
    assert execution_entry_reachability(decision_price=10, trigger_price=11) == "TRIGGER_NOT_REACHED"
    assert execution_entry_reachability(decision_price=12, trigger_price=11) == "TRIGGER_ALREADY_PASSED_AT_DECISION"
    assert execution_entry_reachability(decision_price=10, trigger_price=11, same_bar_ambiguous=True) == "INTRABAR_ORDER_UNKNOWN"
    assert execution_entry_reachability(decision_price=None, trigger_price=11) == "INSUFFICIENT_DATA"


def test_phase_five_capacity_and_adjusted_r_are_explicit_research_proxies():
    assert participation_capacity(10, 1000, .01)["capacity_status"] == "PASS"
    assert participation_capacity(20, 1000, .01)["capacity_status"] == "FAIL"
    assert participation_capacity(10, None, .01)["capacity_status"] == "INSUFFICIENT_VOLUME_DATA"
    assert participation_capacity(None, 1000, .01)["capacity_status"] == "INVALID_SIZE"
    models = target_realism_models(hit=True, stop_first=False, ambiguous=True)
    assert models["TOUCH_MODEL"] is True
    assert models["CONSERVATIVE_AMBIGUITY_MODEL"] is False
    assert models["NEXT_BAR_CONFIRMATION_MODEL"] is None
    adjusted = execution_adjusted_r(reference_entry=10, structural_stop=9, original_r=2, slippage_bps=10)
    assert round(adjusted["stressed_entry"], 4) == 10.01
    assert adjusted["execution_adjusted_R"] < 2


def test_phase_five_normalized_execution_evidence_refines_final_report(tmp_path):
    source = row(date(2026, 8, 7))
    source["detected_timestamp"] = "2026-08-07T14:42:00+00:00"
    source["trigger_price"] = "10.9"
    source["structural_stop"] = "10"
    source["features"]["values"]["extension"]["price_at_detection"] = "10.5"
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    bars_after_decision = [
        {"symbol": "ABC", "timestamp": "2026-08-07T14:43:00+00:00", "open": "11", "high": "11.2", "low": "10.1", "close": "11.1", "volume": "100"},
        {"symbol": "ABC", "timestamp": "2026-08-07T14:44:00+00:00", "open": "11.1", "high": "11.2", "low": "11", "close": "11.1", "volume": "100"},
    ]
    (normalized / "ABC_2026-08-07.jsonl").write_text(
        "\n".join(json.dumps(item) for item in bars_after_decision) + "\n", encoding="utf-8")
    result = full_research_report_streaming(lambda: iter((source,)), normalized_path=normalized)
    overall = result["execution_research"]["strategy"]["FIRST_PULLBACK"]["overall"]
    assert overall["entry_reachability"]["TRIGGER_REACHED"] == 1
    target = result["execution_research"]["strategy"]["FIRST_PULLBACK"]["target_realism"]["2"]
    assert target["coverage_percent"] == 100.0
    assert target["TOUCH_MODEL"] == 1.0
    assert target["NEXT_BAR_CONFIRMATION_MODEL"] == 0.0
    assert result["execution_research"]["evidence"]["bar_count"] == 2

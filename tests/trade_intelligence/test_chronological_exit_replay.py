from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from dataclasses import asdict
import json

import pytest

from app.trade_intelligence.knowledge.exit_replay import (
    EARLY_PARTIAL_POLICIES, POLICY_SETS, ExitPolicy, compare, main, replay_long,
)
from app.trade_intelligence.knowledge.models import HistoricalBar

START = datetime(2026, 8, 13, 14, 30, tzinfo=UTC)


def bar(minute, open="10", high="10.5", low="9.5", close="10"):
    return HistoricalBar("ABC", START + timedelta(minutes=minute),
                         D(open), D(high), D(low), D(close), D("100"), "REGULAR")


def replay(bars, policy=ExitPolicy("STOP"), **kwargs):
    return replay_long(bars, symbol="ABC", entry_time=START,
                       entry_price=D("10"), initial_stop=D("9"),
                       quantity=D("10"), policy=policy, **kwargs)


def test_stop_prevents_profit_from_later_rebound():
    result = replay([bar(0, low="8.5"), bar(1, high="100", close="100")])
    assert result.status == "CLOSED"
    assert result.net_pnl == D("-10")
    assert len(result.fills) == 1


def test_opening_gap_stop_uses_worse_open_price():
    result = replay([bar(0, open="8", high="8.5", low="7.5", close="8")])
    assert result.net_pnl == D("-20")
    assert result.fills[0].price == D("8")


def test_same_bar_target_and_stop_remain_unresolved():
    policy = ExitPolicy("PARTIAL", partial_fraction=D("0.5"))
    result = replay([bar(0, high="12", low="8")], policy)
    assert result.status == "UNRESOLVED_INTRABAR_ORDER"
    assert result.net_pnl is None and result.remaining == 10
    assert not result.fills


def test_open_above_target_establishes_target_before_later_stop():
    policy = ExitPolicy("PARTIAL", partial_fraction=D("0.5"))
    result = replay([bar(0, open="12", high="12", low="8", close="10")], policy)
    assert [f.reason for f in result.fills] == ["PARTIAL_TARGET", "STOP"]
    assert result.net_pnl == 0


def test_trailing_stop_does_not_apply_its_future_high_to_same_bar_low():
    policy = ExitPolicy("TRAIL", partial_fraction=D("0.5"),
                        break_even_after_partial=True, trail_distance_r=D("1"))
    result = replay([bar(0, high="12", low="9.5", close="11.5"),
                     bar(1, open="10.5", high="11", low="10", close="10.5")], policy)
    assert result.status == "CLOSED"
    assert result.fills[-1].timestamp == START + timedelta(minutes=1)
    assert result.fills[-1].price == D("10.5")
    assert result.net_pnl == D("7.5")


def test_runner_exits_at_deadline_open_not_future_maximum():
    result = replay([bar(0, high="20"), bar(1, open="10.2", close="10.2")],
                    hold_minutes=1, cost_per_share_per_side=D("0.01"))
    assert result.fills[-1].reason == "MAX_HOLD"
    assert result.fills[-1].price == D("10.2")
    assert result.net_pnl == D("1.80")
    assert result.cost_paid == D("0.20")


def test_missing_minute_cannot_hide_a_stop():
    result = replay([bar(0), bar(2)], hold_minutes=1)
    assert result.status == "UNRESOLVED_MISSING_BARS"
    assert result.net_pnl is None


def test_truncated_sample_does_not_invent_a_close():
    result = replay([bar(0, high="100")])
    assert result.status == "OPEN_AT_DATA_END"
    assert result.net_pnl is None


def test_entry_inside_bar_does_not_use_pre_entry_high_or_low():
    result = replay_long([bar(0)], symbol="ABC", entry_time=START + timedelta(seconds=30),
                         entry_price=D("10"), initial_stop=D("9"), quantity=D("10"),
                         policy=ExitPolicy("STOP"))
    assert result.status == "UNRESOLVED_ENTRY_BAR"


def test_unordered_bars_are_rejected():
    with pytest.raises(ValueError, match="UNORDERED"):
        replay([bar(0), bar(0)])


def test_fractional_shares_are_not_silently_created():
    policy = ExitPolicy("PARTIAL", partial_fraction=D("0.25"))
    result = replay_long([bar(0, high="11.5"), bar(1)], symbol="ABC", entry_time=START,
                         entry_price=D("10"), initial_stop=D("9"), quantity=D("3"),
                         policy=policy, hold_minutes=1)
    assert len(result.fills) == 1 and result.fills[0].quantity == 3


def test_comparison_uses_identical_entry_and_costs():
    results = compare([bar(0), bar(1)], symbol="ABC", entry_time=START,
                      entry_price=D("10"), initial_stop=D("9"), quantity=D("10"),
                      hold_minutes=1, cost_per_share_per_side=D("0.01"))
    assert len(results) == 3
    assert all(r.net_pnl == D("-0.20") for r in results)


def test_cli_reads_only_requested_episode_batch(tmp_path, capsys):
    corpus = tmp_path / "corpus_repaired"
    corpus.mkdir()
    normalized = tmp_path / "normalized"
    normalized.mkdir()
    episode = {"episode_id": "e1", "symbol": "ABC", "trading_date": "2026-08-13",
               "strategy_memberships": ["HIGH_OF_DAY_BREAKOUT"],
               "trigger_price": "10", "structural_stop": "9",
               "detected_timestamp": START.isoformat()}
    (corpus / "episodes.jsonl").write_text(json.dumps(episode) + "\nNOT_JSON\n")
    path = normalized / "ABC_2026-08-13.jsonl"
    path.write_text("\n".join(json.dumps(asdict(b), default=str) for b in (bar(0), bar(1))) + "\n")
    assert main([str(tmp_path), "--max-scan", "1", "--hold-minutes", "1"]) == 0
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["scanned_rows"] == 1
    assert summary["paired_closed_episodes"] == 1
    assert all(D(value) == D("-0.50") for value in summary["paired_pnl_totals"].values())


def test_cli_rejects_malformed_bars_instead_of_silently_skipping(tmp_path):
    (tmp_path / "corpus_repaired").mkdir()
    (tmp_path / "normalized").mkdir()
    episode = {"episode_id": "e1", "symbol": "ABC", "trading_date": "2026-08-13",
               "strategy_memberships": ["HIGH_OF_DAY_BREAKOUT"],
               "trigger_price": "10", "structural_stop": "9",
               "detected_timestamp": START.isoformat()}
    (tmp_path / "corpus_repaired/episodes.jsonl").write_text(json.dumps(episode) + "\n")
    (tmp_path / "normalized/ABC_2026-08-13.jsonl").write_text("NOT_JSON\n")
    with pytest.raises(ValueError, match="INVALID_OR_OVERSIZED"):
        main([str(tmp_path)])


def batch_fixture(root, episodes, bars=None):
    (root / "corpus_repaired").mkdir()
    (root / "normalized").mkdir()
    rows = [{"episode_id": name, "symbol": "ABC", "trading_date": "2026-08-13",
             "strategy_memberships": ["HIGH_OF_DAY_BREAKOUT", "MICRO_PULLBACK"],
             "trigger_price": "10", "structural_stop": "9",
             "detected_timestamp": START.isoformat()} for name in episodes]
    source = root / "corpus_repaired/episodes.jsonl"
    source.write_bytes(b"".join((json.dumps(row, ensure_ascii=False) + "\r\n").encode() for row in rows))
    (root / "normalized/ABC_2026-08-13.jsonl").write_text(
        "\n".join(json.dumps(asdict(b), default=str) for b in (bars or [bar(0), bar(1)])) + "\n")
    return source


def test_checkpoint_resumes_at_next_row_with_utf8_and_crlf(tmp_path, capsys):
    source = batch_fixture(tmp_path, ["épisode", "second"])
    checkpoint = tmp_path / "cursor.json"
    reports = tmp_path / "reports"
    args = [str(tmp_path), "--max-episodes", "1", "--hold-minutes", "1",
            "--checkpoint", str(checkpoint), "--report-dir", str(reports)]
    main(args)
    first = json.loads(capsys.readouterr().out.splitlines()[-1])
    main(args)
    output = capsys.readouterr().out.splitlines()
    second = json.loads(output[-1])
    assert json.loads(output[-2])["episode_id"] == "second"
    assert second["start_byte_offset"] == first["next_byte_offset"]
    assert second["source_rows_consumed"] == 2
    assert second["next_byte_offset"] == source.stat().st_size and second["at_eof"]
    assert len(list(reports.glob("coverage_*.json"))) == 2
    report = json.loads((reports / f"coverage_{second['start_byte_offset']}_{second['next_byte_offset']}.json").read_text())
    assert report["episodes"][0]["episode_id"] == "second"


@pytest.mark.parametrize("change", ["source", "policy"])
def test_checkpoint_rejects_changed_source_or_policy(tmp_path, change):
    source = batch_fixture(tmp_path, ["first", "second"])
    checkpoint = tmp_path / "cursor.json"
    args = [str(tmp_path), "--max-episodes", "1", "--checkpoint", str(checkpoint)]
    main(args)
    original = checkpoint.read_bytes()
    if change == "source":
        with source.open("ab") as f:
            f.write(b"\n")
    else:
        args += ["--hold-minutes", "30"]
    with pytest.raises(SystemExit):
        main(args)
    assert checkpoint.read_bytes() == original


def test_failed_batch_does_not_advance_checkpoint(tmp_path):
    batch_fixture(tmp_path, ["first", "second"])
    checkpoint = tmp_path / "cursor.json"
    args = [str(tmp_path), "--max-episodes", "1", "--checkpoint", str(checkpoint)]
    main(args)
    original = checkpoint.read_bytes()
    (tmp_path / "normalized/ABC_2026-08-13.jsonl").write_text("NOT_JSON\n")
    with pytest.raises(ValueError):
        main(args)
    assert checkpoint.read_bytes() == original


def test_coverage_groups_overlap_without_double_counting_total(tmp_path, capsys):
    batch_fixture(tmp_path, ["first"])
    main([str(tmp_path), "--strategy", "ALL", "--hold-minutes", "1", "--summary-only"])
    output = capsys.readouterr().out.splitlines()
    summary = json.loads(output[-1])
    assert len(output) == 3
    assert summary["coverage"]["selected_episodes"] == 1
    assert len(summary["by_date_strategy"]) == 2
    assert all(g["paired_closed_episodes"] == 1 for g in summary["by_date_strategy"])
    assert summary["normalized_partition_hashes"]


def test_symbol_cap_skips_are_reported_and_cursor_consumes_them(tmp_path, capsys):
    source = batch_fixture(tmp_path, ["first", "second", "third"])
    main([str(tmp_path), "--max-per-symbol", "1", "--summary-only"])
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["selected_episodes"] == 1
    assert summary["scanned_rows"] == 3
    assert summary["skipped_rows"]["PER_SYMBOL_BATCH_LIMIT"] == 2
    assert summary["next_byte_offset"] == source.stat().st_size


def test_individually_closed_policy_not_in_paired_totals(tmp_path, capsys):
    batch_fixture(tmp_path, ["first"], [bar(0, high="12", low="9.5", close="11.5"),
        bar(1, open="10.5", high="11", low="10", close="10.5"), bar(3)])
    main([str(tmp_path), "--summary-only"])
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    statuses = summary["coverage"]["status_counts"]
    assert statuses["HALF_AT_1R_THEN_1R_TRAIL"] == {"CLOSED": 1}
    assert statuses["STRUCTURAL_STOP_AND_TIME"] == {"UNRESOLVED_MISSING_BARS": 1}
    assert summary["paired_closed_episodes"] == 0
    assert all(D(value) == 0 for value in summary["paired_pnl_totals"].values())


def test_checkpoint_cannot_overwrite_corpus(tmp_path):
    source = batch_fixture(tmp_path, ["first"])
    original = source.read_bytes()
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--checkpoint", str(source)])
    assert source.read_bytes() == original


def test_report_write_failure_does_not_advance_checkpoint(tmp_path, monkeypatch):
    from app.trade_intelligence.knowledge import exit_replay
    batch_fixture(tmp_path, ["first", "second"])
    checkpoint = tmp_path / "cursor.json"
    args = [str(tmp_path), "--max-episodes", "1", "--checkpoint", str(checkpoint)]
    main(args)
    original = checkpoint.read_bytes()
    def fail_write(*args):
        raise OSError("Disk full")
    monkeypatch.setattr(exit_replay, "save_json", fail_write)
    with pytest.raises(OSError, match="Disk full"):
        main(args + ["--report-dir", str(tmp_path / "reports")])
    assert checkpoint.read_bytes() == original


def test_checkpoint_rejects_offset_inside_a_row(tmp_path):
    batch_fixture(tmp_path, ["first", "second"])
    checkpoint = tmp_path / "cursor.json"
    args = [str(tmp_path), "--max-episodes", "1", "--checkpoint", str(checkpoint)]
    main(args)
    data = json.loads(checkpoint.read_text())
    data["next_byte_offset"] -= 1
    checkpoint.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        main(args)


def test_early_partial_preserves_profit_before_reversal_after_half_r():
    path = [bar(0, high="10.6", low="9.5", close="10.5"),
            bar(1, open="10.1", high="10.2", low="8.9", close="9")]
    results = compare(path, policies=EARLY_PARTIAL_POLICIES, symbol="ABC",
        entry_time=START, entry_price=D("10"), initial_stop=D("9"), quantity=D("8"),
        cost_per_share_per_side=D("0.01"))
    later, earlier = results
    assert later.net_pnl == D("-8.16")
    assert earlier.net_pnl == D("0.84")
    assert [(f.quantity, f.price) for f in earlier.fills] == [(D(2), D("10.5")), (D(6), D(10))]
    assert earlier.fills[-1].timestamp == START + timedelta(minutes=1)
    assert earlier.cost_paid == later.cost_paid == D("0.16")


def test_earlier_partial_can_reduce_profit_on_a_continued_winner():
    path = [bar(0, high="11", low="9.5", close="11"),
            bar(1, open="12", high="12", low="12", close="12")]
    later, earlier = compare(path, policies=EARLY_PARTIAL_POLICIES, symbol="ABC",
        entry_time=START, entry_price=D("10"), initial_stop=D("9"), quantity=D("8"),
        hold_minutes=1, cost_per_share_per_side=D("0.01"))
    assert later.net_pnl == D("13.34")
    assert earlier.net_pnl == D("12.84")
    assert all(r.fills[-1].reason == "MAX_HOLD" for r in (later, earlier))


def test_early_partial_does_not_move_break_even_against_same_bar_low():
    earlier = EARLY_PARTIAL_POLICIES[1]
    result = replay_long([bar(0, high="10.6", low="9.2", close="10.4")],
        symbol="ABC", entry_time=START, entry_price=D("10"), initial_stop=D("9"),
        quantity=D("8"), policy=earlier)
    assert result.status == "OPEN_AT_DATA_END"
    assert result.remaining == 6 and result.last_stop == 10
    assert len(result.fills) == 1 and result.net_pnl is None


def test_small_early_partial_position_does_not_invent_fractional_sale_or_lock():
    result = replay_long([bar(0, high="10.8"), bar(1, open="9.5", low="9.5")],
        symbol="ABC", entry_time=START, entry_price=D("10"), initial_stop=D("9"),
        quantity=D("3"), policy=EARLY_PARTIAL_POLICIES[1], hold_minutes=1)
    assert result.net_pnl == D("-1.5")
    assert result.last_stop == 9
    assert len(result.fills) == 1 and result.fills[0].reason == "MAX_HOLD"


def test_early_pair_uses_common_closed_cohort_when_other_policies_are_unresolved(tmp_path, capsys):
    batch_fixture(tmp_path, ["first"], [bar(0, high="10.8", close="10.6"),
        bar(1, open="10.2", high="10.3", low="9.5", close="10.1"), bar(3)])
    main([str(tmp_path), "--policy-set", "EARLY_PARTIAL_V1", "--summary-only"])
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["paired_closed_episodes"] == 0
    pair = summary["early_partial_comparison"]
    assert pair["paired_closed_episodes"] == 1
    assert pair["excluded_from_paired_totals"] == 0
    assert pair["paired_pnl_totals"] == {
        "QUARTER_AT_075R_THEN_BREAK_EVEN": "4.00",
        "QUARTER_AT_050R_THEN_BREAK_EVEN": "2.50",
    }
    assert len(summary["coverage"]["status_counts"]) == 5
    assert len(pair["status_counts"]) == 2


def test_single_early_policy_closed_is_excluded_from_early_pair(tmp_path, capsys):
    batch_fixture(tmp_path, ["first"], [bar(0, high="10.6", close="10.5"),
        bar(1, open="10.2", high="10.3", low="9.5", close="10.1"), bar(3)])
    main([str(tmp_path), "--policy-set", "EARLY_PARTIAL_V1", "--summary-only"])
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    pair = summary["early_partial_comparison"]
    assert pair["status_counts"]["QUARTER_AT_050R_THEN_BREAK_EVEN"] == {"CLOSED": 1}
    assert pair["status_counts"]["QUARTER_AT_075R_THEN_BREAK_EVEN"] == {"UNRESOLVED_MISSING_BARS": 1}
    assert pair["paired_closed_episodes"] == 0 and pair["excluded_from_paired_totals"] == 1
    assert all(D(v) == 0 for v in pair["paired_pnl_totals"].values())


def test_existing_baseline_checkpoint_resumes_without_configuration_change(tmp_path, capsys):
    batch_fixture(tmp_path, ["first", "second"])
    checkpoint = tmp_path / "cursor.json"
    args = [str(tmp_path), "--max-episodes", "1", "--hold-minutes", "1",
            "--checkpoint", str(checkpoint), "--summary-only"]
    main(args)
    first = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert first["configuration"] == {
        "strategy": "HIGH_OF_DAY_BREAKOUT", "hold_minutes": 1,
        "risk_dollars": "25", "cost_per_share_per_side": "0.01",
        "max_per_symbol": 100, "policy_version": "CHRONOLOGICAL_BAR_EXITS_V1",
    }
    main(args + ["--policy-set", "BASELINE"])
    second = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert second["source_rows_consumed"] == 2
    assert "early_partial_comparison" not in second


def test_new_policy_set_rejects_old_checkpoint_and_records_policy_definitions(tmp_path, capsys):
    batch_fixture(tmp_path, ["first", "second"])
    checkpoint = tmp_path / "cursor.json"
    args = [str(tmp_path), "--max-episodes", "1", "--checkpoint", str(checkpoint)]
    main(args)
    original = checkpoint.read_bytes()
    with pytest.raises(SystemExit):
        main(args + ["--policy-set", "EARLY_PARTIAL_V1"])
    assert checkpoint.read_bytes() == original
    new_checkpoint = tmp_path / "early.json"
    main([str(tmp_path), "--policy-set", "EARLY_PARTIAL_V1", "--max-episodes", "1",
          "--checkpoint", str(new_checkpoint), "--summary-only"])
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["configuration"]["policy_set"] == "EARLY_PARTIAL_V1"
    assert len(summary["configuration"]["policies"]) == 5
    assert summary["configuration"]["policies"][-1]["target_r"] == "0.50"
    assert len(POLICY_SETS["BASELINE"]) == 3

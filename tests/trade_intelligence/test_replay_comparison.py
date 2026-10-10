from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
import pytest

from app.trade_intelligence.knowledge.exit_replay import main as replay_main
from app.trade_intelligence.knowledge.models import HistoricalBar
from app.trade_intelligence.knowledge.replay_comparison import adapt_reports, main


@pytest.fixture
def report(tmp_path, capsys):
    (tmp_path / "corpus_repaired").mkdir()
    (tmp_path / "normalized").mkdir()
    start = datetime(2026, 8, 13, 14, 30, tzinfo=UTC)
    episode = dict(episode_id="one", symbol="ABC", trading_date="2026-08-13",
                   strategy_memberships=["HIGH_OF_DAY_BREAKOUT"], trigger_price="10",
                   structural_stop="9", detected_timestamp=start.isoformat())
    (tmp_path / "corpus_repaired/episodes.jsonl").write_text(json.dumps(episode) + "\n")
    bars = [HistoricalBar("ABC", start + timedelta(minutes=i), D("10"), D("10.2"),
                          D("9.8"), D("10"), D("100"), "REGULAR") for i in range(2)]
    (tmp_path / "normalized/ABC_2026-08-13.jsonl").write_text(
        "\n".join(json.dumps(asdict(b), default=str) for b in bars) + "\n")
    replay_main([str(tmp_path), "--policy-set", "EARLY_PARTIAL_V1", "--hold-minutes", "1",
                 "--report-dir", str(tmp_path / "reports"), "--summary-only"])
    capsys.readouterr()
    return json.loads(next((tmp_path / "reports").glob("coverage_*.json")).read_text())


def test_real_replay_report_adapts_without_reclassifying_evidence(report):
    before = deepcopy(report)
    out = adapt_reports([report])
    summary = out["summary"]
    assert summary["evidence_kind"] == "MINUTE_BAR_PROXY"
    assert summary["promotion"] == "NONE"
    engine = summary["engines"][0]
    assert engine["engine"] == "WARRIOR_MOMENTUM_V1"
    assert engine["paired_closed_episodes"] == 1
    assert len(engine["policies"]) == 5
    assert all(D(p["paired_net_pnl"]) == D("-0.50") for p in engine["policies"])
    assert report == before


def test_sparse_and_no_entry_preserve_reasons_and_null_totals(report):
    results = report["episodes"][0]["results"]
    results[0].update(status="UNRESOLVED_MISSING_BARS", net_pnl=None, remaining="25")
    results[1].update(status="NO_ENTRY_MISSING_NEXT_MINUTE", net_pnl=None, remaining="0")
    out = adapt_reports([report])
    assert out["summary"]["engines"][0]["paired_closed_episodes"] == 0
    assert out["summary"]["engines"][0]["policies"][0]["paired_net_pnl"] is None
    assert out["episode_coverage"][0]["outcomes"][results[0]["policy"]] == "UNRESOLVED_MISSING_BARS"


def test_two_policy_subset_is_explicit_and_versioned(report):
    chosen = [r["policy"] for r in report["episodes"][0]["results"][-2:]]
    out = adapt_reports([report], selected_policies=chosen, champion=chosen[0])
    policies = out["summary"]["engines"][0]["policies"]
    assert len(policies) == 2
    assert policies[0]["policy"].endswith(chosen[0])
    assert out["summary"]["experiment_id"] != adapt_reports([report])["summary"]["experiment_id"]


def test_report_content_changes_dataset_identity(report):
    changed = deepcopy(report)
    changed["episodes"][0]["results"][0]["net_pnl"] = "1.25"
    assert adapt_reports([changed])["summary"]["dataset_id"] != adapt_reports([report])["summary"]["dataset_id"]


def test_batches_are_not_counted_twice(report):
    with pytest.raises(ValueError, match="Overlapping"):
        # No episodes here isolates overlap validation from duplicate result validation.
        empty = deepcopy(report)
        empty.update(episodes=[], selected_episodes=0)
        adapt_reports([empty, empty])


def test_noncontiguous_batches_are_flagged_and_order_independent(report):
    other = deepcopy(report)
    other["start_byte_offset"] = report["next_byte_offset"] + 1
    other["next_byte_offset"] = other["start_byte_offset"] + 10
    other["episodes"][0]["episode_id"] = "two"
    out = adapt_reports([other, report])
    assert out["provenance"]["noncontiguous_batches"] == 1
    assert out["summary"]["engines"][0]["paired_closed_episodes"] == 2
    assert out["summary"] == adapt_reports([report, other])["summary"]


@pytest.mark.parametrize("mutation", [
    "configuration", "source", "version", "count", "partition", "definitions",
    "status", "remaining", "missing_policy", "naive_time", "unresolved_pnl", "cost",
])
def test_incompatible_or_inconsistent_reports_rejected(report, mutation):
    other = deepcopy(report)
    other["start_byte_offset"] = report["next_byte_offset"]
    other["next_byte_offset"] += 10
    other["episodes"][0]["episode_id"] = "two"
    episode = other["episodes"][0]
    if mutation == "configuration": other["configuration"]["risk_dollars"] = "30"
    elif mutation == "source": other["source"]["mtime_ns"] += 1
    elif mutation == "version": episode["policy_version"] = "future-v99"
    elif mutation == "count": other["selected_episodes"] = 2
    elif mutation == "partition": episode["normalized_sha256"] = "0" * 64
    elif mutation == "definitions": other["configuration"]["policies"][0]["target_r"] = "9"
    elif mutation == "status": episode["results"][0]["status"] = "UNKNOWN"
    elif mutation == "remaining": episode["results"][0]["remaining"] = "2"
    elif mutation == "missing_policy": episode["results"].pop()
    elif mutation == "naive_time": episode["entry_time"] = "2026-08-13T14:30:00"
    elif mutation == "unresolved_pnl": episode["results"][0]["status"] = "OPEN_AT_DATA_END"
    elif mutation == "cost": episode["cost_per_share_per_side"] = "0.5"
    with pytest.raises(ValueError):
        adapt_reports([report, other])


def test_cli_reads_existing_batches_and_refuses_overwrite(report, tmp_path, capsys):
    report_dir = tmp_path / "input"
    report_dir.mkdir()
    (report_dir / "coverage_0_1.json").write_text(json.dumps(report), encoding="utf-8-sig")
    output = tmp_path / "comparison.json"
    assert main([str(report_dir), "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["engines"][0]["paired_closed_episodes"] == 1
    contents = output.read_bytes()
    with pytest.raises(SystemExit):
        main([str(report_dir), "--output", str(output)])
    assert output.read_bytes() == contents


def test_no_reports_is_error(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path)])

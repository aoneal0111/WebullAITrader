"""Entry timing and capital/cost bounds for the offline experiment."""
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json

import pytest

from app.trade_intelligence.knowledge.exit_replay import (
    main, next_minute_entry, size_entry,
)
from app.trade_intelligence.knowledge.models import HistoricalBar

START = datetime(2026, 8, 13, 14, 30, tzinfo=UTC)


def bar(minute, open="10", high="10", low="10", close="10"):
    return HistoricalBar("ABC", START + timedelta(minutes=minute),
                         D(open), D(high), D(low), D(close), 100, "REGULAR")


def corpus(root, bars):
    (root / "corpus_repaired").mkdir()
    (root / "normalized").mkdir()
    row = {"episode_id": "first", "symbol": "ABC", "trading_date": "2026-08-13",
           "strategy_memberships": ["HIGH_OF_DAY_BREAKOUT"],
           "trigger_price": "10", "structural_stop": "9",
           "detected_timestamp": START.isoformat()}
    (root / "corpus_repaired/episodes.jsonl").write_text(json.dumps(row) + "\n")
    (root / "normalized/ABC_2026-08-13.jsonl").write_text(
        "\n".join(json.dumps(asdict(b), default=str) for b in bars) + "\n")


def test_tight_stop_sizing_accounts_for_both_cost_sides():
    s = size_entry(D("7.18"), D("7.17"), D(25), D("0.01"),
                   "COST_INCLUSIVE", D(50000))
    assert s["quantity"] == 833
    assert s["planned_price_risk"] == D("8.33")
    assert s["estimated_round_trip_cost"] == D("16.66")
    assert s["planned_stop_loss_with_cost"] == D("24.99")


def test_cap_reserves_entry_cost_and_records_actual_purchase_size():
    s = size_entry(D("7.18"), D("7.17"), D(25), D("0.01"),
                   "COST_INCLUSIVE", D(2500))
    assert s["quantity"] == 347
    assert s["purchase_notional"] == D("2491.46")
    assert s["entry_cash_required"] == D("2494.93")
    assert (s["quantity"] + 1) * D("7.19") > D(2500)
    assert s["planned_stop_loss_with_cost"] == D("10.41")


def test_cost_inclusive_share_bound_and_unaffordable_zero_size():
    assert size_entry(D(10), D("9.999"), D(100), D(0),
                      "COST_INCLUSIVE", D(1000000))["quantity"] == 10000
    assert size_entry(D(10), D(9), D(25), D("0.01"),
                      "COST_INCLUSIVE", D(5))["quantity"] == 0


def test_price_risk_default_preserves_original_quantity():
    s = size_entry(D("7.18"), D("7.17"), D(25), D("0.01"))
    assert s["quantity"] == 2500
    assert s["planned_stop_loss_with_cost"] == 75


def test_next_minute_uses_exact_open_and_not_planned_entry_or_prior_high():
    audit = next_minute_entry([bar(0, high="100"), bar(1, open="12", high="12", low="12", close="12")],
        symbol="ABC", detected_time=START + timedelta(seconds=59),
        planned_price=D(10), initial_stop=D(9))
    assert audit["entry_time"] == START + timedelta(minutes=1)
    assert audit["entry_price"] == 12 and audit["open_minus_planned"] == 2
    assert audit["status"] == "ENTRY_OPEN_PROXY"


def test_missing_exact_next_minute_does_not_jump_to_later_bar():
    audit = next_minute_entry([bar(0), bar(2)], symbol="ABC", detected_time=START,
                             planned_price=D(10), initial_stop=D(9))
    assert audit["status"] == "NO_ENTRY_MISSING_NEXT_MINUTE"
    assert audit["entry_price"] is None


@pytest.mark.parametrize("opening", ["9", "8"])
def test_open_at_or_below_structural_stop_is_not_an_immediate_loss_trade(opening):
    audit = next_minute_entry([bar(1, open=opening, high=opening, low=opening, close=opening)],
        symbol="ABC", detected_time=START, planned_price=D(10), initial_stop=D(9))
    assert audit["status"] == "NO_ENTRY_OPEN_AT_OR_BELOW_STOP"
    assert audit["observed_open"] == D(opening)


def test_entry_audit_rejects_bad_order_and_naive_time():
    with pytest.raises(ValueError, match="UNORDERED"):
        next_minute_entry([bar(1), bar(0)], symbol="ABC", detected_time=START,
                          planned_price=D(10), initial_stop=D(9))
    with pytest.raises(ValueError, match="ENTRY_TIME"):
        next_minute_entry([], symbol="ABC", detected_time=START.replace(tzinfo=None),
                          planned_price=D(10), initial_stop=D(9))


def test_cli_reprices_resizes_and_excludes_detection_bar_extrema(tmp_path, capsys):
    corpus(tmp_path, [bar(0, high="100"), bar(1, open="12", high="12.4", low="11.9", close="12"),
                      bar(2, open="12", high="12", low="12", close="12")])
    reports = tmp_path / "reports"
    main([str(tmp_path), "--policy-set", "EARLY_PARTIAL_V1", "--entry-model", "NEXT_MINUTE_OPEN",
          "--sizing-mode", "COST_INCLUSIVE", "--capital-cap", "2500", "--hold-minutes", "1",
          "--report-dir", str(reports), "--summary-only"])
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    e = json.loads(next(reports.glob("coverage_*.json")).read_text())["episodes"][0]
    assert e["entry_price"] == "12" and e["planned_entry_price"] == "10"
    assert e["quantity"] == "8" and e["entry_time"] == (START + timedelta(minutes=1)).isoformat()
    assert e["sizing"]["planned_stop_loss_with_cost"] == "24.16"
    assert summary["entry_status_counts"] == {"ENTRY_OPEN_PROXY": 1}
    assert summary["early_partial_comparison"]["paired_closed_episodes"] == 1
    for r in e["results"]:
        assert r["net_pnl"] == "-0.16"
        assert len(r["fills"]) == 1 and r["fills"][0]["reason"] == "MAX_HOLD"


@pytest.mark.parametrize("bars,cap,status", [
    ([bar(0), bar(2)], "2500", "NO_ENTRY_MISSING_NEXT_MINUTE"),
    ([bar(1, open="8", high="8", low="8", close="8")], "2500", "NO_ENTRY_OPEN_AT_OR_BELOW_STOP"),
    ([bar(1)], "5", "NO_ENTRY_QUANTITY_OUTSIDE_LIMIT"),
])
def test_no_entry_retained_in_coverage_without_cost_fills_or_profit(tmp_path, capsys, bars, cap, status):
    corpus(tmp_path, bars)
    reports = tmp_path / "reports"
    main([str(tmp_path), "--policy-set", "EARLY_PARTIAL_V1", "--entry-model", "NEXT_MINUTE_OPEN",
          "--sizing-mode", "COST_INCLUSIVE", "--capital-cap", cap,
          "--report-dir", str(reports), "--summary-only"])
    s = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert s["selected_episodes"] == 1 and s["entry_status_counts"] == {status: 1}
    assert s["early_partial_comparison"]["paired_closed_episodes"] == 0
    e = json.loads(next(reports.glob("coverage_*.json")).read_text())["episodes"][0]
    assert e["quantity"] == "0" and e["entry_price"] is None and e["sizing"] is None
    assert all(r["net_pnl"] is None and r["cost_paid"] == "0" and not r["fills"] for r in e["results"])


@pytest.mark.parametrize("change", [
    ["--capital-cap", "2000"], ["--entry-model", "PLANNED_PRE_BAR"],
    ["--sizing-mode", "PRICE_RISK"],
])
def test_entry_sizing_checkpoint_rejects_changed_model_or_cap(tmp_path, change):
    corpus(tmp_path, [bar(0), bar(1), bar(2)])
    checkpoint = tmp_path / "cursor.json"
    args = [str(tmp_path), "--entry-model", "NEXT_MINUTE_OPEN", "--sizing-mode", "COST_INCLUSIVE",
            "--capital-cap", "2500", "--checkpoint", str(checkpoint), "--summary-only"]
    main(args)
    original = checkpoint.read_bytes()
    with pytest.raises(SystemExit):
        main(args + change)
    assert checkpoint.read_bytes() == original


@pytest.mark.parametrize("args", [
    ["--sizing-mode", "COST_INCLUSIVE"], ["--capital-cap", "NaN"],
    ["--capital-cap", "0"], ["--capital-cap", "Infinity"],
])
def test_cli_requires_explicit_finite_positive_cap_before_reading_source(tmp_path, args):
    with pytest.raises(SystemExit):
        main([str(tmp_path)] + args)

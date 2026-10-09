from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from dataclasses import asdict
import json

import pytest

from app.trade_intelligence.knowledge.exit_replay import ExitPolicy, compare, main, replay_long
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

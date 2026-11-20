"""Batch analysis counts observed signals separately from realized outcomes."""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.trade_intelligence.knowledge.capture_profit_batch import (
    MAX_FILES, summarize_captures,
)

LIFE = "WARRIOR_MOMENTUM_V1|TEST|episode"


def stamp(second):
    return f"2026-10-09T15:00:{second:02d}+00:00"


def sample(*, life=LIFE, sell="9", quotes=(("10.45", 10), ("10.20", 20))):
    orders = []
    records = []
    for index, (side, price, second) in enumerate(
        (("BUY", "10", 0), ("SELL", sell, 50))
    ):
        oid = f"{life}-order-{index}"
        fill = {
            "fill_id": f"{life}-fill-{index}",
            "quantity": "10", "price": price, "commission": "0",
            "timestamp": stamp(second),
        }
        orders.append({
            "order_id": oid, "paper_campaign_id": "campaign",
            "filled_quantity": "10", "fills": [fill],
            "request": {
                "symbol": "TEST", "side": side,
                "strategy_lifecycle_id": life,
                "structural_stop_price": "9" if side == "BUY" else None,
            },
        })
        records.append({
            "timestamp": stamp(second), "symbol": "TEST",
            "payload": {
                "action": "FILL", "lifecycle_id": life,
                "paper_campaign_id": "campaign", "order_id": oid,
                "fill_timestamp": stamp(second), "side": side,
                "quantity": "10", "price": price,
            },
        })
    for bid, second in quotes:
        records.append({
            "timestamp": stamp(second), "symbol": "TEST",
            "payload": {
                "action": "QUOTE", "lifecycle_id": life,
                "paper_campaign_id": "campaign",
                "quote_timestamp": stamp(second), "bid": bid,
                "ask": str(D(bid) + D("0.01")),
                "stale_quote": False, "future_quote": False,
            },
        })
    return {"orders": orders, "capture_records": records}


def scenario(result, a="0.40", retention="0.50"):
    return next(
        r for r in result["scenarios"]
        if r["activation_r"] == D(a)
        and r["peak_retention"] == D(retention)
    )


def test_batch_distinguishes_winning_and_losing_trade_signals():
    loss = sample()
    win = sample(
        life=LIFE + "-win", sell="11",
        quotes=(("10.8", 10), ("10.7", 20)),
    )
    report = summarize_captures([loss, win])
    row = scenario(report)
    assert report["capture_files"] == 2
    assert report["unique_lifecycles"] == 2
    assert row["reconciled"] == 2
    assert row["actual_winners"] == 1
    assert row["actual_losers"] == 1
    assert row["actual_net_pnl"] == D("0")
    assert row["losing_trade_signals"] == 1
    assert row["winning_trade_signals"] == 0
    assert row["armed_without_signal"] == 1
    assert row["hypothetical_net_pnl"] is None
    assert row["unknown_execution_fill_count"] is None
    assert report["runtime_policy_changed"] is False


def test_unreconciled_trade_is_not_counted_as_a_winner_or_signal():
    data = sample()
    data["orders"][0]["filled_quantity"] = "11"
    r = scenario(summarize_captures([data]))
    assert r["reconciled"] == 0
    assert r["excluded_unreconciled"] == 1
    assert r["actual_net_pnl"] == 0
    assert r["observed_signals"] == 0


def test_duplicate_lifecycle_across_captures_is_rejected():
    a = sample()
    with pytest.raises(ValueError, match="DUPLICATE_LIFECYCLE_ACROSS_CAPTURES"):
        summarize_captures([a, deepcopy(a)])


def test_cost_sensitivity_does_not_rewrite_actual_pnl():
    data = sample()
    base = scenario(summarize_captures([data]))
    costly = scenario(summarize_captures(
        [data], extra_cost_per_side=D("0.10"),
    ))
    assert base["actual_net_pnl"] == costly["actual_net_pnl"] == D("-10")
    assert base["losing_trade_signals"] == 1
    assert costly["losing_trade_signals"] == 0


def test_limits_and_invalid_settings_still_fail_closed():
    with pytest.raises(ValueError, match="INVALID_CAPTURE_COUNT"):
        summarize_captures([])
    with pytest.raises(ValueError, match="INVALID_CAPTURE_COUNT"):
        summarize_captures([sample()] * (MAX_FILES + 1))
    with pytest.raises(ValueError, match="INVALID_EXPERIMENT_SETTINGS"):
        summarize_captures([sample()], max_quote_age_seconds=D("-1"))


def test_no_input_mutation():
    data = sample()
    original = deepcopy(data)
    summarize_captures([data])
    assert data == original

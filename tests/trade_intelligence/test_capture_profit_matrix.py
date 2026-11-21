"""Matrix must be descriptive only and preserve actual ledger outcomes."""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.trade_intelligence.knowledge.capture_profit_matrix import (
    ACTIVATIONS, RETENTIONS, compare_capture,
)
LIFE = "WARRIOR_MOMENTUM_V1|TEST|episode"


def stamp(second):
    return f"2026-10-09T15:00:{second:02d}+00:00"


def sample(quotes=None):
    orders, records = [], []
    for index, (side, qty, price, second) in enumerate(
        [("BUY", "10", "10", 0), ("SELL", "10", "9", 50)]
    ):
        oid = f"order-{index}"
        fill = {
            "fill_id": f"fill-{index}",
            "quantity": qty, "price": price,
            "timestamp": stamp(second), "commission": "0",
        }
        orders.append({
            "order_id": oid, "paper_campaign_id": "campaign",
            "filled_quantity": qty, "fills": [fill],
            "request": {
                "symbol": "TEST", "side": side,
                "strategy_lifecycle_id": LIFE,
                "structural_stop_price": "9" if side == "BUY" else None,
            },
        })
        records.append({
            "timestamp": fill["timestamp"], "symbol": "TEST",
            "payload": {
                "action": "FILL", "lifecycle_id": LIFE,
                "paper_campaign_id": "campaign", "order_id": oid,
                "fill_timestamp": fill["timestamp"],
                "side": side, "quantity": qty, "price": price,
            },
        })
    for second, bid in (quotes or [(10, "10.50"), (20, "10.20")]):
        records.append({
            "timestamp": stamp(second), "symbol": "TEST",
            "payload": {
                "action": "QUOTE", "lifecycle_id": LIFE,
                "paper_campaign_id": "campaign",
                "quote_timestamp": stamp(second), "bid": bid,
                "ask": str(D(bid) + D("0.01")),
                "stale_quote": False, "future_quote": False,
            },
        })
    return {"orders": orders, "capture_records": records}


def scenario(matrix, activation, retention):
    return next(s for s in matrix["scenarios"]
        if s["activation_r"] == D(activation)
        and s["peak_retention"] == D(retention))


def test_naut_like_small_peak_arms_earlier_without_inventing_fills():
    # 10 shares, entry 10, stop 9 => initial risk $10.
    # Peak bid 10.45 => observed peak +0.45R; actual later loss.
    data = sample(quotes=[(10, "10.45"), (20, "10.20")])
    matrix = compare_capture(data)
    assert len(matrix["scenarios"]) == len(ACTIVATIONS) * len(RETENTIONS)
    early = scenario(matrix, "0.40", "0.50")
    assert early["armed"] == 1
    assert early["observed_signals"] == 1
    assert early["lifecycles"][0]["actual_closed_pnl"] == D("-10")
    assert early["lifecycles"][0]["signal_bid"] == D("10.20")
    for activation in ("0.50", "0.75", "1.50"):
        result = scenario(matrix, activation, "0.50")
        assert result["armed"] == 0
        assert result["observed_signals"] == 0
    assert all(r["hypothetical_pnl"] is None
        and r["hypothetical_fill_price"] is None
        for s in matrix["scenarios"] for r in s["lifecycles"])
    assert matrix["runtime_execution_enabled"] is False


def test_future_profit_does_not_retroactively_arm_or_signal():
    matrix = compare_capture(sample(quotes=[
        (10, "10.10"), (20, "10.00"), (30, "10.80"),
    ]))
    candidate = scenario(matrix, "0.40", "0.50")
    assert candidate["armed"] == 1
    assert candidate["observed_signals"] == 0


def test_unreconciled_lifecycle_is_excluded_from_signal_counts():
    data = sample()
    data["orders"][0]["filled_quantity"] = "11"
    result = scenario(compare_capture(data), "0.25", "0.50")
    assert result["reconciled"] == 0
    assert result["not_reconciled"] == 1
    assert result["armed"] == 0
    assert result["observed_signals"] == 0
    assert "ORDER_FILL_QUANTITY_MISMATCH" in (
        result["lifecycles"][0]["reconciliation_problems"]
    )


def test_gap_in_sampled_quotes_remains_explicit_and_never_fills():
    data = sample(quotes=[(10, "10.45"), (20, "10.20")])
    quote = next(r for r in data["capture_records"]
        if r["payload"]["action"] == "QUOTE")
    quote["payload"]["missing_interval"] = True
    quote["payload"]["interval_seconds"] = "20"
    result = scenario(compare_capture(data), "0.40", "0.50")
    assert result["lifecycles"][0]["missing_interval_flags"] == 1
    assert result["lifecycles"][0]["hypothetical_pnl"] is None


def test_additional_costs_change_signal_eligibility_not_actual_fills():
    data = sample(quotes=[(10, "10.45"), (20, "10.20")])
    normal = scenario(compare_capture(data), "0.40", "0.50")
    costed = scenario(compare_capture(data, extra_cost_per_side=D(".1")), "0.40", "0.50")
    assert normal["armed"] == 1
    assert costed["armed"] == 0
    assert normal["lifecycles"][0]["actual_closed_pnl"] == (
        costed["lifecycles"][0]["actual_closed_pnl"]
    )


def test_input_limits_and_invalid_settings_remain_enforced():
    with pytest.raises(ValueError, match="CAPTURE_INPUT_LIMIT"):
        compare_capture({"orders": [], "capture_records": [{}] * 5001})
    with pytest.raises(ValueError, match="INVALID_EXPERIMENT_SETTINGS"):
        compare_capture(sample(), max_quote_age_seconds=D("-1"))


def test_mutating_input_copy_does_not_change_original():
    data = sample()
    before = deepcopy(data)
    compare_capture(data)
    assert data == before

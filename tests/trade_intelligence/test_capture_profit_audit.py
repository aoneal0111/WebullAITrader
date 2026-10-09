"""Signal evidence must reconcile and cannot imply counterfactual fills."""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.trade_intelligence.knowledge.capture_profit_audit import audit_capture, instant

LIFE = "WARRIOR_MOMENTUM_V1|TEST|episode"


def stamp(second):
    return f"2026-10-09T15:00:{second:02d}+00:00"


def sample(sales=None, quotes=None):
    orders, records = [], []
    for index, (side, qty, price, second) in enumerate(
            [("BUY", "10", "10", 0)] + (sales or [("SELL", "10", "9", 50)])):
        oid = f"order-{index}"
        f = dict(fill_id=f"fill-{index}", quantity=qty, price=price,
                 timestamp=stamp(second), commission="0")
        orders.append(dict(order_id=oid, paper_campaign_id="campaign", filled_quantity=qty,
            fills=[f], request=dict(symbol="TEST", side=side, strategy_lifecycle_id=LIFE,
                structural_stop_price="9" if side == "BUY" else None)))
        records.append(dict(timestamp=f["timestamp"], symbol="TEST", payload=dict(
            action="FILL", lifecycle_id=LIFE, paper_campaign_id="campaign", order_id=oid,
            fill_timestamp=f["timestamp"], side=side, quantity=qty, price=price)))
    for second, bid in (quotes or [(10, "10.5"), (20, "10.2")]):
        records.append(dict(timestamp=stamp(second), symbol="TEST", payload=dict(
            action="QUOTE", lifecycle_id=LIFE, paper_campaign_id="campaign",
            quote_timestamp=stamp(second), bid=bid, ask=str(D(bid) + D(".01")),
            stale_quote=False, future_quote=False)))
    return dict(orders=orders, capture_records=records)


def result(data=None, **kwargs):
    return audit_capture(data or sample(), **kwargs)["lifecycles"][0]


def test_signal_overlay_does_not_invent_profit_or_fills():
    r = result()
    assert r["actual_closed_pnl"] == -10
    assert r["first_observed_signal"]["total_profit_mark"] == 2
    assert r["first_observed_signal"]["observed_peak_profit"] == 5
    assert r["candidate_fill_pnl"] is None


def test_future_peak_does_not_retroactively_arm_signal():
    r = result(sample(quotes=[(10, "10.2"), (20, "10.1"), (30, "11")]))
    assert r["candidate_armed_at"]["evaluated_at"] == instant(stamp(30))
    assert r["first_observed_signal"] is None


def test_actual_partials_are_in_total_trade_mark():
    r = result(sample(sales=[("SELL", "5", "11", 15), ("SELL", "5", "10", 50)],
                      quotes=[(10, "10.5"), (20, "10.4")]))
    assert r["sampled_peak_total_profit"]["total_profit_mark"] == 7
    assert r["actual_closed_pnl"] == 5
    assert r["first_observed_signal"] is None


def test_future_timestamp_excluded_despite_false_flag_and_clamped_age():
    data = sample()
    p = data["capture_records"][2]["payload"]
    p.update(quote_timestamp="2026-10-09T15:00:10.025+00:00", quote_age_seconds=0)
    r = result(data)
    assert r["excluded_quotes"]["FUTURE_QUOTE"] == 1
    assert r["future_timestamp_ms"]["max"] == D("25")
    assert r["candidate_armed_at"] is None


@pytest.mark.parametrize("change,reason", [
    ({"quote_timestamp":stamp(1)}, "STALE_QUOTE"),
    ({"bid":None}, "MISSING_SIDES"),
    ({"bid":"11", "ask":"10"}, "INVALID_QUOTE_GEOMETRY"),
    ({"quote_timestamp":stamp(0)}, "SAME_TIME_FILL_ORDER_UNKNOWN"),
])
def test_quote_exclusions(change, reason):
    data = sample()
    r = data["capture_records"][2]
    r["payload"].update(change)
    if reason == "SAME_TIME_FILL_ORDER_UNKNOWN":
        r["timestamp"] = stamp(0)
    assert result(data)["excluded_quotes"][reason] == 1


def test_gap_is_evidence_limit_not_fill_estimate():
    data = sample()
    data["capture_records"][3]["payload"].update(missing_interval=True, interval_seconds="10")
    r = result(data)
    assert r["missing_interval_flags"] == 1
    assert r["max_recorded_interval_seconds"] == 10
    assert r["candidate_fill_pnl"] is None


def test_extra_cost_separate_from_recorded_fees_and_can_prevent_arming():
    r = result(extra_cost_per_side=D(".1"))
    assert r["actual_closed_pnl"] == -10
    assert r["closed_pnl_with_declared_extra_cost"] == -12
    assert r["recorded_commissions"] == 0
    assert r["candidate_armed_at"] is None


@pytest.mark.parametrize("mutation,problem", [
    (lambda d:d["capture_records"].pop(0), "CAPTURE_LEDGER_FILL_MISMATCH"),
    (lambda d:d["orders"][0].update(filled_quantity="11"), "ORDER_FILL_QUANTITY_MISMATCH"),
    (lambda d:d["orders"][0]["request"].update(structural_stop_price=None), "ENTRY_STRUCTURAL_STOP_MISSING"),
    (lambda d:d["orders"][1]["fills"][0].update(fill_id="fill-0"), "DUPLICATE_FILL"),
])
def test_incomplete_reconciliation_never_emits_signal(mutation, problem):
    data = sample()
    mutation(data)
    r = result(data)
    assert r["status"] == "NOT_RECONCILED"
    assert problem in r["reconciliation_problems"]
    assert "first_observed_signal" not in r


def test_campaigns_not_combined_and_scalper_excluded():
    data = sample()
    other = deepcopy(data)
    for o in other["orders"]:
        o["paper_campaign_id"] = "other"
        o["order_id"] += "-other"
    for r in other["capture_records"]:
        r["payload"]["paper_campaign_id"] = "other"
        if "order_id" in r["payload"]:
            r["payload"]["order_id"] += "-other"
    data["orders"] += other["orders"]
    data["capture_records"] += other["capture_records"]
    data["capture_records"].append(dict(timestamp=stamp(1),symbol="TEST",
        payload=dict(action="QUOTE", lifecycle_id="QUICK_SCALPER|TEST|episode")))
    assert len(audit_capture(data)["lifecycles"]) == 2


def test_overlapping_entry_exit_phase_is_not_supported():
    data = sample()
    data["orders"][1]["fills"][0]["timestamp"] = stamp(0)
    data["capture_records"][1]["payload"]["fill_timestamp"] = stamp(0)
    assert "OVERLAPPING_ENTRY_EXIT_PHASES" in result(data)["reconciliation_problems"]


@pytest.mark.parametrize("setting", [dict(activation_r=0), dict(peak_retention=1),
    dict(max_quote_age_seconds=-1), dict(extra_cost_per_side="NaN")])
def test_invalid_settings(setting):
    with pytest.raises(ValueError):
        audit_capture(sample(), **setting)


def test_duplicate_orders_and_oversized_capture_rejected():
    data = sample()
    data["orders"].append(deepcopy(data["orders"][0]))
    with pytest.raises(ValueError, match="DUPLICATE_ORDER"):
        audit_capture(data)
    with pytest.raises(ValueError, match="CAPTURE_INPUT_LIMIT"):
        audit_capture(dict(orders=[],capture_records=[{}]*5001))


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError, match="NAIVE_TIMESTAMP"):
        instant("2026-10-09T15:00:00")


def test_arming_waits_for_final_entry_fill():
    data = sample()
    entry = data["orders"][0]
    entry["fills"][0]["quantity"] = "5"
    extra = dict(entry["fills"][0],fill_id="later",quantity="5",timestamp=stamp(15))
    entry["fills"].append(extra)
    data["capture_records"][0]["payload"]["quantity"] = "5"
    record = deepcopy(data["capture_records"][0])
    record["timestamp"] = stamp(15)
    record["payload"]["fill_timestamp"] = stamp(15)
    data["capture_records"].append(record)
    r = result(data)
    assert r["candidate_armed_at"] is None
    assert r["first_observed_signal"] is None


def test_recorded_commissions_reduce_marks_and_closed_pnl():
    data = sample()
    for o in data["orders"]:
        o["fills"][0]["commission"] = "1"
    r = result(data)
    assert r["actual_closed_pnl"] == -12
    assert r["recorded_commissions"] == 2
    assert r["candidate_armed_at"]["total_profit_mark"] == 4
    assert r["first_observed_signal"]["total_profit_mark"] == 1

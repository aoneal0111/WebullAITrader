from copy import deepcopy
from decimal import Decimal as D

import pytest
from app.trade_intelligence.knowledge.capture_exit_comparison import compare_captures


def sample():
    life = "WARRIOR_MOMENTUM_V1|TEST|episode"
    def stamp(s):
        return f"2026-10-09T15:00:{s:02d}+00:00"
    orders, records = [], []
    for i, (side, price, sec) in enumerate((("BUY", "10", 0), ("SELL", "9", 50))):
        fill = dict(fill_id=str(i), quantity="10", price=price, commission="0", timestamp=stamp(sec))
        orders.append(dict(order_id=str(i), paper_campaign_id="c", filled_quantity="10", fills=[fill],
            request=dict(symbol="TEST", side=side, strategy_lifecycle_id=life,
                         structural_stop_price="9" if side == "BUY" else None)))
        records.append(dict(timestamp=stamp(sec), symbol="TEST", payload=dict(action="FILL",
            paper_campaign_id="c", lifecycle_id=life, order_id=str(i), side=side,
            quantity="10", price=price, fill_timestamp=stamp(sec))))
    for sec, bid in ((1, "10.5"), (2, "10.2"), (3, "9")):
        records.append(dict(timestamp=stamp(sec), symbol="TEST", payload=dict(action="QUOTE",
            paper_campaign_id="c", lifecycle_id=life, quote_timestamp=stamp(sec), bid=bid,
            ask=str(D(bid) + D(".01")))))
    return dict(orders=orders, capture_records=records)


def results(data=None, **kwargs):
    return compare_captures([data or sample()], **kwargs)["lifecycles"][0]["results"]


def test_same_actual_entry_two_proxy_exits_actual_pnl_separate():
    report = compare_captures([sample()])
    assert report["lifecycles"][0]["actual_closed_pnl"] == "-10"
    base, peak = report["lifecycles"][0]["results"]
    assert base["net_pnl"] == "-10"
    assert peak["net_pnl"] == "2.0"
    assert peak["reason"] == "PEAK_RETENTION"
    assert report["comparison"]["engines"][0]["paired_closed_episodes"] == 1
    assert report["promotion"] == "NONE"


@pytest.mark.parametrize("change,reason", [
    ({"quote_timestamp": "2026-10-09T15:00:02+00:00"}, "FUTURE_QUOTE"),
    ({"stale_quote": True}, "STALE_QUOTE"),
    ({"missing_interval": True}, "QUOTE_GAP"),
    ({"bid": "12", "ask": "11"}, "INVALID_QUOTE"),
    ({"bid": None}, "INVALID_QUOTE"),
])
def test_invalid_path_does_not_skip_to_later_profit(change, reason):
    data = sample()
    data["capture_records"][2]["payload"].update(change)
    for state in results(data):
        assert state["status"] == "UNRESOLVED"
        assert state["reason"] == reason
        assert state["net_pnl"] is None


def test_gap_from_entry_and_end_of_capture_unresolved():
    assert results(max_gap_seconds=D(".5"))[0]["reason"] == "QUOTE_GAP"
    data = sample()
    data["capture_records"].pop()
    base, peak = results(data)
    assert base["reason"] == "CAPTURE_ENDED"
    assert peak["status"] == "CLOSED"


def test_bad_quote_after_peak_exit_does_not_erase_closed_proxy():
    data = sample()
    data["capture_records"][-1]["payload"]["future_quote"] = True
    base, peak = results(data)
    assert base["reason"] == "FUTURE_QUOTE"
    assert peak["net_pnl"] == "2.0"


def test_observation_order_not_silently_sorted():
    data = sample()
    data["capture_records"][3], data["capture_records"][4] = data["capture_records"][4], data["capture_records"][3]
    # Structural closes before the bad timestamp; peak policy also closes on stop.
    data["capture_records"][3]["payload"].update(bid="10.3", ask="10.31")
    assert results(data)[0]["reason"] == "NONINCREASING_OBSERVATION"


def test_costs_can_prevent_peak_activation_and_are_charged_once():
    base, peak = results(extra_cost_per_side=D(".2"), exit_fee_per_share=D(".1"))
    assert base["net_pnl"] == "-15.0"
    assert peak["reason"] == "STOP"


def test_duplicate_lifecycle_rejected_and_reconciliation_failure_excluded():
    with pytest.raises(ValueError, match="Duplicate lifecycle"):
        compare_captures([sample(), sample()])
    data = sample()
    data["orders"][0]["filled_quantity"] = "9"
    report = compare_captures([data])
    assert report["lifecycles"][0]["status"] == "NOT_RECONCILED"
    assert report["comparison"]["engines"][0]["paired_closed_episodes"] == 0


@pytest.mark.parametrize("settings", [{"hold_seconds": D("NaN")}, {"peak_retention": D("1")},
                                         {"max_gap_seconds": D("0")}, {"exit_fee_per_share": D("-1")}])
def test_invalid_settings(settings):
    with pytest.raises(ValueError):
        results(**settings)


def test_max_hold_uses_observed_bid_and_no_future_quote():
    base, peak = results(hold_seconds=D("1"))
    assert base["reason"] == peak["reason"] == "MAX_HOLD"
    assert base["net_pnl"] == peak["net_pnl"] == "5.0"


def test_future_peak_does_not_arm_retroactively():
    data = sample()
    for record, bid in zip(data["capture_records"][2:], ("10.2", "10.1", "10.5")):
        record["payload"].update(bid=bid, ask=str(D(bid) + D(".01")))
    assert results(data)[1]["reason"] == "CAPTURE_ENDED"


def test_actual_exit_price_does_not_enter_proxy_state():
    data = sample()
    data["orders"][1]["fills"][0]["price"] = "12"
    data["capture_records"][1]["payload"]["price"] = "12"
    report = compare_captures([data])
    assert report["lifecycles"][0]["actual_closed_pnl"] == "20"
    assert report["lifecycles"][0]["results"][0]["net_pnl"] == "-10"


def test_prospective_coverage_failure_invalidates_only_still_open_proxies():
    data = sample()
    record = deepcopy(data["capture_records"][3])
    record["timestamp"] = "2026-10-09T15:00:02.5+00:00"
    record["payload"] = dict(action="PROFIT_SHADOW_UNAVAILABLE", paper_campaign_id="c",
        lifecycle_id="WARRIOR_MOMENTUM_V1|TEST|episode", reason="NONINCREASING_PROVIDER_TIMESTAMP")
    data["capture_records"].insert(4, record)
    base, peak = results(data)
    assert base["status"] == "UNRESOLVED"
    assert base["reason"] == "NONINCREASING_PROVIDER_TIMESTAMP"
    assert peak["status"] == "CLOSED" and peak["net_pnl"] == "2.0"

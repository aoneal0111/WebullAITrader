from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import pytest
from app.trade_intelligence.knowledge.quote_exit_comparison import compare

START = datetime(2026, 10, 9, tzinfo=timezone.utc)


def quote(second, bid, ask, age=0):
    observed = START + timedelta(seconds=second)
    return dict(observed_at=observed.isoformat(),
                source_at=(observed - timedelta(seconds=age)).isoformat(), bid=bid, ask=ask)


def run(rows, **kwargs):
    return compare(rows, signal_at=START, stop=D("0.9"), **kwargs)


def test_next_quote_ask_entry_bid_exit_multiplier_and_both_fees():
    result = run([quote(0, "1", "1.01"), quote(1, "0.99", "1"),
                  quote(2, "1.06", "1.07"), quote(3, "1.11", "1.12")],
                 multiplier=D("100"), fee_per_unit_per_side=D("0.65"))
    assert result["entry_at"] == (START + timedelta(seconds=1)).isoformat()
    assert result["quantity"] == 2
    assert result["paired_closed"]
    assert [r["net_pnl_proxy"] for r in result["results"]] == ["9.40", "19.40"]


@pytest.mark.parametrize("row,status", [(quote(10,"1.2","1.3"), "UNRESOLVED_QUOTE_GAP"),
    (quote(2,"1.2","1.3",3), "UNRESOLVED_QUOTE_AGE"),
    (quote(2,"1.2","1.3",-0.1), "UNRESOLVED_QUOTE_AGE")])
def test_invalid_path_does_not_invent_profit(row, status):
    result = run([quote(1,"0.99","1"),row])
    assert not result["paired_closed"]
    assert all(r["status"] == status and r["net_pnl_proxy"] is None for r in result["results"])


def test_stop_gap_can_exceed_risk_budget():
    result = run([quote(1,"0.99","1"),quote(2,"0.7","0.8")])
    assert result["results"][0]["exit_reason"] == "STOP"
    assert D(result["results"][0]["net_pnl_proxy"]) < -D("25")


def test_unordered_invalid_and_oversized_quotes_rejected():
    for rows in ([quote(2,"1","1"),quote(1,"1","1")],
                 [quote(1,"NaN","1")], [quote(1,"2","1")],
                 [quote(i,"1","1") for i in range(10001)]):
        with pytest.raises(ValueError):
            run(rows)


def test_no_entry_and_zero_size():
    assert run([quote(0,"1","1")])["status"] == "NO_FRESH_ENTRY"
    assert run([quote(6,"1","1")])["status"] == "NO_FRESH_ENTRY"
    assert run([quote(1,"0.99","1")], capital_cap=D("0.1"))["status"] == "NO_EXECUTABLE_SIZE"


def test_empty_policies_do_not_claim_paired_closure():
    with pytest.raises(ValueError):
        run([], targets=())


def test_target_after_hold_deadline_is_not_classified_as_target():
    result = run([quote(1,"0.99","1"), quote(2,"1.2","1.3")], hold_seconds=1)
    assert all(r["exit_reason"] == "MAX_HOLD" for r in result["results"])

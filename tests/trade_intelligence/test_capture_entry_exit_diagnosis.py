"""Diagnosis uses reconciled fills, exact auth joins, sampled quote evidence."""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from app.trade_intelligence.knowledge.capture_entry_exit_diagnosis import (
    diagnose_captures,
)

LIFE = "WARRIOR_MOMENTUM_V1|TEST|episode"


def stamp(second):
    return f"2026-10-09T15:00:{second:02d}+00:00"


def capture(*, entry_context=False, decision_state="TRIGGERED"):
    orders, records = [], []
    for index, (side, price, second) in enumerate(
        (("BUY", "10", 0), ("SELL", "9", 50))
    ):
        oid = f"order-{index}"
        fill = {
            "fill_id": f"fill-{index}", "quantity": "10",
            "price": price, "commission": "0", "timestamp": stamp(second),
        }
        orders.append({
            "order_id": oid, "created_at": stamp(5 if side == "BUY" else 49),
            "paper_campaign_id": "campaign", "filled_quantity": "10",
            "fills": [fill], "request": {
                "symbol": "TEST", "side": side, "strategy_lifecycle_id": LIFE,
                "structural_stop_price": "9" if side == "BUY" else None,
            },
        })
        records.append({
            "timestamp": stamp(second), "symbol": "TEST",
            "payload": {
                "action": "FILL", "lifecycle_id": LIFE,
                "paper_campaign_id": "campaign", "order_id": oid,
                "fill_timestamp": stamp(second),
                "side": side, "quantity": "10", "price": price,
            },
        })
    for second, bid in ((10, "10.45"), (20, "10.20")):
        records.append({
            "timestamp": stamp(second), "symbol": "TEST",
            "payload": {
                "action": "QUOTE", "lifecycle_id": LIFE,
                "paper_campaign_id": "campaign",
                "quote_timestamp": stamp(second), "bid": bid,
                "ask": str(D(bid) + D(".01")),
                "stale_quote": False, "future_quote": False,
            },
        })
    result = {"orders": orders, "capture_records": records}
    if entry_context:
        result["entry_context"] = {"records": [
            {
                "timestamp": stamp(3), "symbol": "TEST",
                "record_type": "STATE_TRANSITION",
                "payload": {
                    "to": "AUTHORIZATION_EVALUATED",
                    "lifecycle_id": LIFE, "paper_campaign_id": "campaign",
                    "authorization_result": "AUTHORIZED",
                    "configuration_fingerprint": "fp",
                    "setup": "HIGH_OF_DAY_BREAKOUT", "trigger": "10.00",
                    "structural_stop": "9",
                },
            },
            {
                "timestamp": stamp(2), "symbol": "TEST",
                "record_type": "DECISION", "payload": {
                    "evaluation_timestamp": stamp(2),
                    "paper_campaign_id": "campaign",
                    "configuration_fingerprint": "fp",
                    "setup": {
                        "type": "HIGH_OF_DAY_BREAKOUT",
                        "state": decision_state, "trigger": "10",
                    },
                },
            },
        ]}
    return result


def test_missing_authorization_cannot_be_called_bad_entry():
    report = diagnose_captures([capture()])
    row = report["trades"][0]
    assert row["entry_evidence_status"] == "NO_MATCHED_AUTHORIZATION"
    assert row["actual_closed_pnl"] == D("-10")
    assert row["candidate_signal_bid"] == D("10.20")
    assert row["hypothetical_fill_pnl"] is None
    assert row["causal_entry_defect_proven"] is False
    assert report["summary"]["missing_matching_authorization"] == 1


def test_exact_authorization_and_trigger_match():
    data = capture(entry_context=True)
    old = deepcopy(data)
    row = diagnose_captures([data])["trades"][0]
    assert row["entry_evidence_status"] == "MOST_RECENT_DECISION_MATCHED"
    assert row["entry_details"][0]["authorized_setup"] == "HIGH_OF_DAY_BREAKOUT"
    assert row["entry_details"][0]["current_confirmation_matches"] is True
    assert data == old


def test_forming_decision_is_mismatch_not_proven_cause():
    row = diagnose_captures([
        capture(entry_context=True, decision_state="FORMING")
    ])["trades"][0]
    assert row["entry_evidence_status"] == "MOST_RECENT_DECISION_MISMATCH"
    assert row["causal_entry_defect_proven"] is False


def test_future_auth_decision_cannot_qualify_pre_entry():
    data = capture(entry_context=True)
    data["entry_context"]["records"][0]["timestamp"] = stamp(6)
    row = diagnose_captures([data])["trades"][0]
    assert row["entry_evidence_status"] == "NO_MATCHED_AUTHORIZATION"


def test_incomplete_ledger_excluded_from_pnl_and_signal_counts():
    data = capture(entry_context=True)
    data["orders"][0]["filled_quantity"] = "11"
    report = diagnose_captures([data])
    assert report["summary"]["reconciled"] == 0
    assert report["summary"]["unreconciled"] == 1
    assert report["summary"]["actual_closed_net_pnl"] == 0
    assert report["trades"][0]["ledger_status"] == "NOT_RECONCILED"


def test_duplicate_lifecycle_and_invalid_capture_count_fail_closed():
    data = capture()
    with pytest.raises(ValueError, match="DUPLICATE_LIFECYCLE_ACROSS_CAPTURES"):
        diagnose_captures([data, deepcopy(data)])
    with pytest.raises(ValueError, match="INVALID_CAPTURE_COUNT"):
        diagnose_captures([])

from copy import deepcopy

import pytest

from app.trade_intelligence.knowledge.capture_entry_audit import audit_entries


def capture():
    life = "WARRIOR_MOMENTUM_V1|ABC|episode"
    auth = {"to": "AUTHORIZATION_EVALUATED", "lifecycle_id": life,
            "authorization_result": "AUTHORIZED", "configuration_fingerprint": "config",
            "setup": "HIGH_OF_DAY_BREAKOUT", "trigger": "10.00"}
    records = [{"timestamp": "2026-10-09T15:01:00+00:00", "symbol": "ABC",
                "record_type": "STATE_TRANSITION", "payload": auth}]
    for clock, state, trigger in [("15:00:00", "TRIGGERED", "10"), ("15:00:59", "FORMING", "11")]:
        records.append({"timestamp": f"2026-10-09T{clock}+00:00", "symbol": "ABC", "record_type": "DECISION",
                        "payload": {"evaluation_timestamp": f"2026-10-09T{clock}+00:00",
                                    "configuration_fingerprint": "config",
                                    "setup": {"type": "HIGH_OF_DAY_BREAKOUT", "state": state, "trigger": trigger}}})
    return {"orders": [{"order_id": "order", "created_at": "2026-10-09T15:01:01+00:00",
                        "filled_quantity": "10", "paper_campaign_id": "campaign",
                        "request": {"side": "BUY", "symbol": "ABC", "strategy_lifecycle_id": life}}],
            "entry_context": {"records": records}}


def test_retained_evidence_and_numeric_trigger_equivalence():
    data = capture()
    original = deepcopy(data)
    row = audit_entries(data)["entries"][0]
    assert row["current_state"] == "FORMING"
    assert row["matching_confirmation_age_seconds"] == "60.0"
    assert row["current_confirmation_matches"] is False
    assert data == original


def test_later_evaluation_is_not_pre_entry_evidence():
    data = capture()
    data["entry_context"]["records"][-1]["payload"]["evaluation_timestamp"] = "2026-10-09T15:01:02+00:00"
    assert audit_entries(data)["entries"][0]["current_confirmation_matches"] is True


@pytest.mark.parametrize("field,value", [("configuration_fingerprint", "other"), ("evaluation_timestamp", None)])
def test_unmatched_or_untimed_decision_excluded(field, value):
    data = capture()
    data["entry_context"]["records"][-1]["payload"][field] = value
    assert audit_entries(data)["entries"][0]["current_state"] == "TRIGGERED"


def test_wrong_lifecycle_cannot_authorize():
    data = capture()
    data["entry_context"]["records"][0]["payload"]["lifecycle_id"] = "other"
    assert audit_entries(data)["entries"][0]["status"] == "AUTHORIZATION_EVIDENCE_MISSING"


def test_missing_current_evidence_is_not_a_rejection():
    data = capture()
    data["entry_context"]["records"] = data["entry_context"]["records"][:1]
    row = audit_entries(data)["entries"][0]
    assert row["status"] == "CURRENT_DECISION_EVIDENCE_MISSING"
    assert "current_confirmation_matches" not in row


def test_record_bounds():
    data = capture()
    data["entry_context"]["records"] *= 334
    with pytest.raises(ValueError, match="INPUT_RECORD_LIMIT"):
        audit_entries(data)


def test_exact_source_decision_is_distinct_from_latest_nearby_state():
    data = capture()
    auth, confirmed, later = data["entry_context"]["records"]
    auth["payload"]["source_decision_id"] = "a" * 24
    confirmed["payload"]["decision_id"] = "a" * 24
    later["payload"]["decision_id"] = "b" * 24
    row = audit_entries(data)["entries"][0]
    assert row["source_decision_status"] == "SOURCE_DECISION_ID_MATCHED"
    assert row["source_decision_state"] == "TRIGGERED"
    assert row["source_decision_matches_authorization"] is True
    assert row["source_decision_age_seconds"] == "60.0"
    assert row["current_confirmation_matches"] is False
    assert row["source_decision_is_gateway_snapshot_proof"] is False


def test_exact_source_decision_mismatch_remains_evidence_only():
    data = capture()
    auth, confirmed, later = data["entry_context"]["records"]
    auth["payload"]["source_decision_id"] = "a" * 24
    confirmed["payload"]["decision_id"] = "a" * 24
    confirmed["payload"]["setup"]["state"] = "FORMING"
    row = audit_entries(data)["entries"][0]
    assert row["source_decision_status"] == "SOURCE_DECISION_ID_MATCHED"
    assert row["source_decision_matches_authorization"] is False
    assert row["source_decision_is_gateway_snapshot_proof"] is False


def test_unavailable_source_decision_has_no_inferred_state_or_age():
    data = capture()
    data["entry_context"]["records"][0]["payload"]["source_decision_id"] = "a" * 24
    row = audit_entries(data)["entries"][0]
    assert row["source_decision_status"] == "SOURCE_DECISION_ID_UNAVAILABLE"
    assert row["source_decision_matches_authorization"] is None
    assert row["source_decision_age_seconds"] is None
    assert row["source_decision_state"] is None

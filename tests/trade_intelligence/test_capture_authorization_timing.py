"""Small bounded synthetic timing comparison tests, no broker/runtime."""
from copy import deepcopy

import pytest

from app.trade_intelligence.knowledge.capture_authorization_timing import (
    compare_diagnosis,
)


def fixture():
    rows = []
    for symbol, pnl, age, quotes in [
        ("FSLY", "-43.92", "30.5", 465),
        ("JAGX", "-17.00", None, 54),
        ("WFF", "-54.48", "104.372243", 8),
        ("AEHL", "43.69", "7.44189", 262),
    ]:
        rows.append({
            "symbol": symbol, "campaign": "paper", "lifecycle": f"WARRIOR_MOMENTUM_V1|{symbol}|episode",
            "ledger_status": "RECONCILED", "entry_evidence_status": "MOST_RECENT_DECISION_MISMATCH",
            "actual_closed_pnl": pnl, "eligible_quotes": quotes,
            "missing_interval_flags": 3, "candidate_signal_at": None,
            "entry_details": [{
                "status": "AUTHORIZATION_MATCHED",
                "authorization_time": "2026-10-09T15:00:00+00:00",
                "entry_created": "2026-10-09T15:00:00.100000+00:00",
                "authorized_setup": "RECLAIM_CONTINUATION",
                "current_state": "NO_SETUP", "current_setup": None,
                "current_confirmation_matches": False,
                "matching_confirmation_age_seconds": age,
            }],
        })
    return {"trades": rows, "summary": {"unreconciled": 0}, "runtime_policy_changed": False}


def test_four_trade_comparison_is_observational():
    data = fixture()
    before = deepcopy(data)
    report = compare_diagnosis(data)
    assert report["summary"]["target_trades"] == 4
    assert report["summary"]["winners"] == 1
    assert report["summary"]["losers"] == 3
    assert report["summary"]["latest_decision_mismatches"] == 4
    assert report["summary"]["with_matching_confirmation_age"] == 3
    assert report["summary"]["max_observed_matching_confirmation_age_seconds"] == "104.372243"
    assert report["runtime_policy_changed"] is False
    assert all(not r["causal_defect_proven"] for r in report["trades"])
    assert data == before


def test_missing_age_is_not_claimed_to_be_stale():
    report = compare_diagnosis(fixture())
    jagx = next(r for r in report["trades"] if r["symbol"] == "JAGX")
    assert jagx["matching_confirmation_age_seconds"] is None
    assert jagx["missing_matching_confirmation"] is True
    assert jagx["proven_gateway_snapshot_identity"] is False


def test_rejects_incomplete_provenance_and_unreconciled_sources():
    data = fixture()
    data["trades"][0]["entry_evidence_status"] = "NO_MATCHED_AUTHORIZATION"
    with pytest.raises(ValueError, match="ENTRY_PROVENANCE_INCOMPLETE"):
        compare_diagnosis(data)
    data = fixture()
    data["summary"]["unreconciled"] = 1
    with pytest.raises(ValueError, match="UNRECONCILED_SOURCE"):
        compare_diagnosis(data)


def test_rejects_order_before_authorization_and_ambiguous_entry():
    data = fixture()
    data["trades"][0]["entry_details"][0]["entry_created"] = "2026-10-09T14:59:59+00:00"
    with pytest.raises(ValueError, match="AUTHORIZATION_AFTER_ORDER"):
        compare_diagnosis(data)
    data = fixture()
    data["trades"][0]["entry_details"].append(data["trades"][0]["entry_details"][0])
    with pytest.raises(ValueError, match="AMBIGUOUS_ENTRY_ORDER"):
        compare_diagnosis(data)


def test_rejects_nonfinite_age_and_missing_symbols():
    data = fixture()
    data["trades"][0]["entry_details"][0]["matching_confirmation_age_seconds"] = "NaN"
    with pytest.raises(ValueError, match="INVALID_CONFIRMATION_AGE"):
        compare_diagnosis(data)
    data = fixture()
    data["trades"].pop()
    with pytest.raises(ValueError, match="MISSING_TARGET_SYMBOLS:AEHL"):
        compare_diagnosis(data)


def test_rejects_duplicate_lifecycle_and_unknown_policy_scope():
    data = fixture()
    data["trades"][1]["lifecycle"] = data["trades"][0]["lifecycle"]
    with pytest.raises(ValueError, match="DUPLICATE_LIFECYCLE"):
        compare_diagnosis(data)
    data = fixture()
    data["runtime_policy_changed"] = None
    with pytest.raises(ValueError, match="SOURCE_POLICY_SCOPE_UNKNOWN"):
        compare_diagnosis(data)

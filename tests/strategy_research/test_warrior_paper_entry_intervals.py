"""Verify entry stage interval attribution only with exact source evidence."""
from copy import deepcopy

import pytest

from app.strategy_research.warrior_paper_entry_intervals import summarize


def sample():
    return {
        "version": "WARRIOR_ENTRY_EXIT_DIAGNOSIS_V1",
        "runtime_policy_changed": False,
        "trades": [{
            "campaign": "paper-1",
            "lifecycle": "WARRIOR_MOMENTUM_V1|XYZ|1",
            "symbol": "XYZ",
            "ledger_status": "RECONCILED",
            "actual_closed_pnl": "-7.5",
            "candidate_signal_at": "2026-10-09T13:30:00+00:00",
            "entry_complete_at": "2026-10-09T13:30:05+00:00",
            "entry_details": [{
                "status": "AUTHORIZATION_MATCHED",
                "authorization_time": "2026-10-09T13:30:02+00:00",
                "entry_created": "2026-10-09T13:30:02.100+00:00",
                "source_decision_status": "SOURCE_DECISION_ID_MATCHED",
                "source_decision_matches_authorization": True,
                "source_decision_timestamp": "2026-10-09T13:30:01+00:00",
            }],
        }],
    }


def test_exact_source_and_observed_stage_intervals():
    report = summarize(sample())
    entry = report["entries"][0]
    assert entry["source_to_authorization_ms"] == 1000
    assert entry["authorization_to_order_ms"] == 100
    assert entry["order_to_last_entry_fill_ms"] == 2900
    assert entry["scanner_to_order_ms"] is None
    assert entry["candidate_signal_to_order_ms"] is None
    assert entry["gateway_snapshot_identity_proven"] is False
    assert report["orders"] == report["network_requests"] == 0


def test_nearby_decision_is_never_promoted_to_source():
    d = sample()
    detail = d["trades"][0]["entry_details"][0]
    detail["source_decision_status"] = "SOURCE_DECISION_ID_NOT_RECORDED"
    detail["source_decision_matches_authorization"] = None
    result = summarize(d)
    assert result["entries"][0]["source_to_authorization_ms"] is None
    assert result["counts"]["EXACT_SOURCE_DECISION_TIMING_UNAVAILABLE"] == 1


def test_reversed_authorization_order_is_invalid():
    d = sample()
    d["trades"][0]["entry_details"][0]["entry_created"] = "2026-10-09T13:30:01+00:00"
    with pytest.raises(ValueError, match="REVERSED_STAGE_TIMESTAMPS"):
        summarize(d)


def test_ambiguous_entry_does_not_create_fake_pair():
    d = sample()
    d["trades"][0]["entry_details"].append(deepcopy(d["trades"][0]["entry_details"][0]))
    result = summarize(d)
    assert result["entries"] == []
    assert result["counts"]["AMBIGUOUS_ENTRY_ORDER_EXCLUDED"] == 1


def test_reject_untrusted_or_duplicate_lifecycles():
    d = sample()
    d["runtime_policy_changed"] = True
    with pytest.raises(ValueError, match="SOURCE_POLICY_SCOPE_UNKNOWN"):
        summarize(d)
    d = sample()
    d["trades"].append(deepcopy(d["trades"][0]))
    with pytest.raises(ValueError, match="MISSING_OR_DUPLICATE_LIFECYCLE"):
        summarize(d)

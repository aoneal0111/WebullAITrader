"""Fail-closed synthetic tests for Atlas four-work-order evidence gates."""
from copy import deepcopy

import pytest

from app.strategy_research.atlas_four_work_orders_evidence import summarize


def sources():
    ledger = {
        "scope": "OFFLINE_ATLAS_PAPER_LEDGER_PROVENANCE_V1",
        "authoritative_ledger_identified": False, "orders": 0,
        "network_requests": 0, "runtime_modified": False,
        "candidates": [{"classification": "PAPER_SCHEMA_WITH_ORDERS",
                        "verified_paper_identity": True}],
    }
    recording = {
        "scope": "CLOSED_RECORDING_EVENT_SHAPE_V1",
        "authoritative_order_ledger_proven": False,
        "fills_reconciled": False, "protective_exits_verified": False,
        "network_requests": 0, "orders_submitted": 0,
        "runtime_modified": False, "candidate_event_lines": 3,
    }
    entry = {
        "version": "WARRIOR_PAPER_ENTRY_INTERVALS_V1",
        "orders": 0, "network_requests": 0, "runtime_policy_changed": False,
        "entries": [{
            "scanner_to_order_ms": None, "candidate_signal_to_order_ms": None,
            "gateway_snapshot_identity_proven": False,
            "source_lineage": "SOURCE_DECISION_MATCHED",
            "authorization_to_order_ms": 18.721,
        }],
    }
    micro = {
        "scope": "WARRIOR_MICRO_COST_SENSITIVITY_V1",
        "source_scope": "RESEARCH_ONLY_WARRIOR_MICRO_NEXT_OPEN_BARRIER_V1",
        "orders": 0, "network_requests": 0, "production_policy_changed": False,
        "observed_trigger_runs": 4, "resolved_cases": 2,
        "unresolved_cases": 2, "resolved_win_count": 1,
        "resolved_loss_count": 1,
        "scenarios": [{"assumed_cost_per_side_per_share": "0.01",
                       "resolved_only_net_r": "-0.5"}],
    }
    return [ledger, recording, entry, micro]


def test_all_four_work_orders_remain_explicitly_unverified():
    report = summarize(*sources())
    assert report["scope"] == "ATLAS_FOUR_WORK_ORDERS_EVIDENCE_V1"
    assert report["ledger_reconciliation"]["paper_schema_candidates"] == 1
    assert report["ledger_reconciliation"]["broker_confirmed_fills"] is None
    assert report["protective_exits"]["recording_stop_execution_proven"] is False
    assert report["entry_timing"]["exact_source_decision_rows"] == 1
    assert report["entry_timing"]["scanner_first_seen_proven"] is False
    assert report["strategy_validation"]["unresolved_or_ambiguous_cases"] == 2
    assert report["strategy_validation"]["strategy_promotion_authorized"] is False
    assert report["safe_for_live_promotion"] is False
    assert report["network_requests"] == report["orders_submitted"] == 0


@pytest.mark.parametrize("index,key,value", [
    (0, "authoritative_ledger_identified", True),
    (1, "fills_reconciled", True),
    (1, "protective_exits_verified", True),
    (2, "network_requests", 1),
    (3, "production_policy_changed", True),
])
def test_unverified_upstream_claims_rejected(index, key, value):
    inputs = sources()
    inputs[index][key] = value
    with pytest.raises(ValueError):
        summarize(*inputs)


@pytest.mark.parametrize("key,value", [
    ("scanner_to_order_ms", 10),
    ("candidate_signal_to_order_ms", 12),
    ("gateway_snapshot_identity_proven", True),
])
def test_unproven_entry_join_rejected(key, value):
    inputs = sources()
    inputs[2]["entries"][0][key] = value
    with pytest.raises(ValueError, match="UNPROVEN_SCANNER_OR_GATEWAY_JOIN"):
        summarize(*inputs)


def test_negative_or_out_of_bounds_entry_duration_rejected():
    for value in (-1, float("nan"), 86_400_001, "10"):
        inputs = sources()
        inputs[2]["entries"][0]["authorization_to_order_ms"] = value
        with pytest.raises(ValueError):
            summarize(*inputs)


def test_outcome_denominator_mismatch_rejected():
    inputs = sources()
    inputs[3]["unresolved_cases"] = 1
    with pytest.raises(ValueError, match="MICRO_OUTCOME_COUNTS_INCONSISTENT"):
        summarize(*inputs)


def test_only_aggregate_counts_exposed_not_sensitive_source_rows():
    inputs = sources()
    inputs[2]["entries"][0]["symbol"] = "PRIVATE_TEST_SYMBOL"
    report = summarize(*inputs)
    assert "PRIVATE_TEST_SYMBOL" not in str(report)
    assert len(report["strategy_validation"]) > 5


def test_does_not_mutate_inputs():
    inputs = sources()
    original = deepcopy(inputs)
    summarize(*inputs)
    assert inputs == original

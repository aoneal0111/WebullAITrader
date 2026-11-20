"""Source-decision IDs are observation-only and never authorize a trade."""
from dataclasses import replace

from app.strategies.warrior_momentum.forward_runtime import (
    _opportunity_authorization_record,
)
from tests.warrior_momentum.test_opportunity_engine import assessment


DECISION = "d" * 24


def record(value, *, authorized=True):
    return _opportunity_authorization_record(
        value,
        authorized=authorized,
        reason="PASS" if authorized else "NOT_AUTHORIZED",
        risk_result="PASSED",
        sizing_result="PASSED",
    ).payload


def test_provenance_tag_records_candidate_decision_identity():
    payload = record(replace(assessment(), source_decision_id=DECISION))
    assert payload["source_decision_id"] == DECISION
    assert payload["generation_id"] == "generation-1"
    assert payload["lifecycle_id"] == "generation-1"
    assert payload["authorization_result"] == "AUTHORIZED"


def test_rejected_authorization_still_tags_source_without_changing_result():
    payload = record(replace(assessment(), source_decision_id=DECISION), authorized=False)
    assert payload["source_decision_id"] == DECISION
    assert payload["authorization_result"] == "REJECTED"


def test_no_decision_identity_is_explicitly_unknown_not_fabricated():
    payload = record(assessment())
    assert payload["source_decision_id"] is None


def test_invalid_or_unbounded_source_identity_is_never_serialized():
    for unsafe in ("hello", "A" * 24, "x" * 24, "a" * 300):
        payload = record(replace(assessment(), source_decision_id=unsafe))
        assert payload["source_decision_id"] is None

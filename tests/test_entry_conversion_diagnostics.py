"""Bounded, payload-free diagnostics for autonomous PAPER entry lifecycles."""

from datetime import UTC, datetime
from decimal import Decimal
import json

from app.performance_diagnostics import PerformanceDiagnostics


def test_lifecycle_and_setup_transition_records_are_bounded_and_reconstructable():
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_setup_transition(
        symbol="TNON", state="SETUP_FORMING", lifecycle_id="life-1",
        setup_type="BULL_FLAG", timestamp=datetime.now(UTC),
        trigger=Decimal("7.82"), stop=Decimal("7.60"), reason="FORMING",
    )
    diagnostics.record_setup_transition(symbol="TNON", state="SETUP_FORMING")
    diagnostics.record_setup_transition(symbol="TNON", state="ENTRY_READY")
    diagnostics.record_entry_lifecycle(
        "life-1", symbol="TNON", requested_quantity=393,
        initial_limit=Decimal("7.823910"), risk_approved=True,
    )
    metrics = diagnostics.entry_conversion_metrics()
    assert len(metrics["lifecycle_records"]) == 1
    assert len(metrics["setup_transitions"]) == 2
    assert metrics["lifecycle_records"][0]["initial_limit"] == "7.823910"


def test_pursuit_distinguishes_invocation_from_refusal_and_bounds_records():
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_entry_counter("pursuit_not_invoked_due_to_state")
    diagnostics.record_pursuit_evaluation(
        reason="CHASE_CEILING", symbol="TNON", lifecycle_id="life-1",
        current_limit=Decimal("7.82"), candidate_replacement=Decimal("7.88"),
    )
    for _ in range(700):
        diagnostics.record_pursuit_evaluation(reason="NO_PRICE_IMPROVEMENT")
    metrics = diagnostics.entry_conversion_metrics()
    assert metrics["counters"]["pursuit_not_invoked_due_to_state"] == 1
    assert metrics["counters"]["pursuit_evaluations"] == 701
    assert metrics["replacement_refusal_counts_by_reason"]["CHASE_CEILING"] == 1
    assert len(metrics["pursuit_evaluations"]) == 512


def test_partial_fill_and_expiry_are_lifecycle_state_not_market_payloads():
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_partial_fill(
        lifecycle_id="life-2", symbol="PCLA", filled_before=0,
        newly_filled=100, filled_quantity=100, remaining_quantity=322,
        average_fill_price=Decimal("13.20"),
    )
    diagnostics.record_entry_counter("entry_expirations")
    diagnostics.record_entry_lifecycle(
        "life-2", expiry_timestamp=datetime.now(UTC),
        expiry_reason="ENTRY_STALE", terminal_state="EXPIRED",
    )
    serialized = json.dumps(diagnostics.entry_conversion_metrics())
    assert "raw_payload" not in serialized
    metrics = diagnostics.entry_conversion_metrics()
    assert metrics["counters"]["partial_fill_events"] == 1
    assert metrics["counters"]["entry_expirations"] == 1
    assert metrics["lifecycle_records"][0]["remaining_quantity"] == 322


def test_durable_artifact_contains_bounded_entry_conversion_section(tmp_path):
    diagnostics = PerformanceDiagnostics()
    path = tmp_path / "entry.json"
    diagnostics.start_run(artifact_path=path, flush_seconds=0.05)
    diagnostics.record_entry_counter("entry_authorizations")
    diagnostics.record_entry_lifecycle("life-3", symbol="FTFT", terminal_state="WORKING")
    diagnostics.finish_run()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entry_conversion"]["counters"]["entry_authorizations"] == 1
    assert payload["entry_conversion"]["bounds"]["lifecycle_records"] == 256


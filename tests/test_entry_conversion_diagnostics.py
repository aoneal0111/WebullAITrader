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


def test_execution_subtype_counters_and_quote_age_survive_record_eviction():
    diagnostics = PerformanceDiagnostics()
    for index in range(700):
        diagnostics.record_entry_funnel(
            "TNON", "EXECUTION_QUOTE_REJECTED", outcome="QUOTE_REJECTED_BID_STALE",
            bid_timestamp_age=Decimal("6.5"), last_trade_timestamp_age=Decimal("1.2"),
            processing_age=Decimal("0.3"), delivery_age=Decimal("0.4"),
        )
    metrics = diagnostics.entry_conversion_metrics()
    assert metrics["execution_gate_counters"]["QUOTE_REJECTED_BID_STALE"] == 700
    assert metrics["quote_age_summary"]["bid_age"]["count"] == 700
    assert metrics["quote_age_summary"]["bid_age"]["p90"] == 6.5
    assert len(metrics["funnel_records"]) == 512


def test_all_gate_counter_families_are_independent_of_bounded_records():
    diagnostics = PerformanceDiagnostics()
    for reason in (
        "ENTRY_PRICE_DISPLACED_PERCENT", "ENTRY_PRICE_DISPLACED_ABSOLUTE",
        "ENTRY_PRICE_DISPLACED_BOTH", "ENTRY_PRICE_INVALID", "ENTRY_PRICE_BELOW_STOP",
        "RISK_REJECTED_STOP_DISTANCE", "RISK_REJECTED_ENGINE", "RISK_REJECTED_EXPOSURE",
        "CLEAR_PRICE_DISPLACEMENT", "CLEAR_QUOTE_FRESHNESS", "CLEAR_RISK",
    ):
        stage = "TECHNICAL_SIGNAL_CLEARED" if reason.startswith("CLEAR_") else "ADAPTIVE_PRICE_GATE"
        diagnostics.record_entry_funnel("TNON", stage, outcome="REJECTED", reason=reason)
    counters = diagnostics.entry_conversion_metrics()["execution_gate_counters"]
    assert counters["ENTRY_PRICE_DISPLACED_PERCENT"] == 1
    assert counters["RISK_REJECTED_EXPOSURE"] == 1
    assert counters["CLEAR_QUOTE_FRESHNESS"] == 1


def test_detector_diagnostics_are_exported_without_raw_payloads(tmp_path):
    class Provider:
        def snapshot(self):
            return {"TNON": {"MOMENTUM_ACCELERATION": {
                "state": "NOT_FORMED", "first_failed_predicate": "LIFETIME_EXCEEDED",
                "summary": {"observation_count": 3, "velocity": "0.4"},
            }}}
        def all_transitions(self):
            return {"TNON": ({"state": "INVALIDATED", "setup_type": "MOMENTUM_ACCELERATION"},)}
        def unique_episode_counts(self):
            return {"MOMENTUM_ACCELERATION": {
                "unique_setup_episodes": 2, "unique_triggered_episodes": 1,
            }}
    diagnostics = PerformanceDiagnostics()
    path = tmp_path / "detectors.json"
    diagnostics.start_run(artifact_path=path, flush_seconds=0.05)
    diagnostics.register_detector_diagnostics(Provider())
    diagnostics.request_checkpoint()
    diagnostics.finish_run()
    payload = json.loads(path.read_text(encoding="utf-8"))
    section = payload["entry_conversion"]
    assert section["detector_diagnostics"]["TNON"]["MOMENTUM_ACCELERATION"]["summary"]["observation_count"] == 3
    assert section["detector_transitions"]["TNON"][0]["state"] == "INVALIDATED"
    assert section["unique_setup_episodes"]["MOMENTUM_ACCELERATION"] == 2
    assert "raw_payload" not in json.dumps(section)


def test_price_gate_samples_and_distributions_survive_eviction(tmp_path):
    diagnostics = PerformanceDiagnostics()
    for index in range(700):
        diagnostics.record_price_gate_sample(
            symbol=f"S{index}", setup_type="RECLAIM_CONTINUATION",
            lifecycle_id=f"life-{index}", evaluation_timestamp=datetime.now(UTC),
            structural_trigger=Decimal("1.560780"), trigger_age_seconds=Decimal("2.5"),
            entry_ready_age_seconds=Decimal("1.2"), signal_age_seconds=Decimal("0.4"),
            ask=Decimal("1.600"), bid=Decimal("1.590"), spread=Decimal("0.010"),
            risk_normalized_extension=Decimal("0.8"), actual_displacement_percent=Decimal("2.5128"),
            remaining_first_target_r=Decimal("0.7"), remaining_final_target_r=Decimal("2.1"),
            continuation_class="RECLAIM", gate_result="ENTRY_PRICE_DISPLACED_PERCENT",
        )
    metrics = diagnostics.entry_conversion_metrics()
    assert len(metrics["price_gate_samples"]) == 512
    assert metrics["price_gate_result_counters"]["ENTRY_PRICE_DISPLACED_PERCENT"] == 700
    assert metrics["price_gate_continuation_rejection_counters"]["RECLAIM"] == 700
    assert metrics["price_gate_distributions"]["actual_displacement_percent"]["count"] == 700
    assert metrics["price_gate_by_setup_type"]["RECLAIM_CONTINUATION"]["result_counters"]["ENTRY_PRICE_DISPLACED_PERCENT"] == 700
    serialized = json.dumps(metrics)
    assert "raw_payload" not in serialized


def test_price_gate_diagnostic_failure_is_contained():
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_price_gate_sample(
        symbol="TNON", setup_type="BULL_FLAG", gate_result="ACCEPTED",
        actual_displacement_percent=object(), lifecycle_id=object(),
    )
    metrics = diagnostics.entry_conversion_metrics()
    assert metrics["price_gate_result_counters"]["ACCEPTED"] == 1


def test_price_gate_artifact_contains_bounded_diagnostics(tmp_path):
    diagnostics = PerformanceDiagnostics()
    path = tmp_path / "price-gate.json"
    diagnostics.start_run(artifact_path=path, flush_seconds=0.05)
    diagnostics.record_price_gate_sample(
        symbol="TGE", setup_type="RECLAIM_CONTINUATION", lifecycle_id="episode-1",
        structural_trigger=Decimal("1.560780"), ask=Decimal("1.600"),
        effective_max_execution_price=Decimal("1.589462"),
        actual_displacement_percent=Decimal("2.5128"),
        gate_result="ENTRY_PRICE_DISPLACED_PERCENT", continuation_class="RECLAIM",
    )
    diagnostics.request_checkpoint()
    diagnostics.finish_run()
    section = json.loads(path.read_text(encoding="utf-8"))["entry_conversion"]
    sample = section["price_gate_samples"][0]
    assert sample["structural_trigger"] == "1.560780"
    assert sample["ask"] == "1.600"
    assert sample["effective_max_execution_price"] == "1.589462"
    assert sample["gate_result"] == "ENTRY_PRICE_DISPLACED_PERCENT"

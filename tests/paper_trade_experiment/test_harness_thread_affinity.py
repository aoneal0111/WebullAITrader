from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from dataclasses import replace

from app.paper_trade_experiment.harness import (
    CONTROL_ARM,
    TREATMENT_ARM,
    ExperimentDefinition,
    ExperimentOpportunity,
    PaperExperimentJournal,
)
from app.strategies.warrior_momentum.models import (
    SetupDetection,
    SetupState,
    SetupType,
    StopModel,
)
from app.trade_intelligence.decision_intelligence.entry_timing import (
    EntryIntelligenceConfig,
    HistoricalPaperEntryTimingPolicy,
    PAPER_TREATMENT,
)
from app.trade_intelligence.decision_intelligence.models import (
    HistoricalIntelligenceResult,
)


NOW = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)


def _definition() -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id="historical_entry_timing",
        version="ATLAS_HISTORICAL_ENTRY_EXPERIMENT_V1",
        strategy="HIGH_OF_DAY_BREAKOUT",
        control_arm={"entry_mode": "CURRENT_TRIGGER"},
        treatment_arm={"entry_mode": "TRIGGER_READY_ENTRY"},
        start_timestamp="2026-01-01T00:00:00+00:00",
        enabled=True,
    )


def _opportunity(identity: str) -> ExperimentOpportunity:
    return ExperimentOpportunity(
        assignment_identity=identity,
        strategy="HIGH_OF_DAY_BREAKOUT",
        symbol="XYZ",
        trading_date="2026-09-15",
        decision_timestamp=NOW.isoformat(),
    )


def _di_result() -> HistoricalIntelligenceResult:
    return HistoricalIntelligenceResult(
        opportunity_id="legacy-opportunity-1",
        trading_date="2026-09-15",
        evaluated_at=NOW,
        recognized_memberships=("HIGH_OF_DAY_BREAKOUT",),
        primary_strategy="HIGH_OF_DAY_BREAKOUT",
        setup_stage="TRIGGER_READY",
        entry_location="APPROACHING_TRIGGER",
        entry_assessment="FAVORABLE_LOCATION",
        trigger_price=Decimal("10"),
        structural_stop=Decimal("9.80"),
        current_price=Decimal("9.95"),
        setup_evidence=(),
        readiness_memberships=(),
    )


def _candidate() -> SimpleNamespace:
    return SimpleNamespace(
        symbol="XYZ",
        setup=SetupDetection(
            SetupType.HIGH_OF_DAY_BREAKOUT,
            SetupState.FORMING,
            Decimal("80"),
            Decimal("10"),
            Decimal("9.80"),
            StopModel.BREAKOUT_LEVEL,
        ),
    )


def test_worker_thread_assignment_uses_per_operation_connection(tmp_path: Path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    definition = _definition()

    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            journal.assignment,
            definition,
            _opportunity("worker-opportunity"),
            mode="PAPER",
            arm=TREATMENT_ARM,
        ).result()

    assert result.persisted
    assert result.reason == "ASSIGNED"
    assert journal._connection.execute(
        "SELECT COUNT(*) FROM experiment_assignments"
    ).fetchone()[0] == 1
    journal.close()


def test_concurrent_different_opportunities_persist_without_lock_errors(tmp_path: Path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    definition = _definition()

    def assign(index: int):
        return journal.assignment(
            definition,
            _opportunity(f"different-{index}"),
            mode="PAPER",
            arm=CONTROL_ARM if index % 2 else TREATMENT_ARM,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(assign, range(32)))

    assert all(result.persisted for result in results)
    assert all(result.reason == "ASSIGNED" for result in results)
    assert journal._connection.execute(
        "SELECT COUNT(*) FROM experiment_assignments"
    ).fetchone()[0] == 32
    journal.close()


def test_same_opportunity_race_has_one_deterministic_assignment(tmp_path: Path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    definition = _definition()
    barrier = Barrier(16)

    def assign(_: int):
        barrier.wait()
        return journal.assignment(
            definition,
            _opportunity("same-opportunity"),
            mode="PAPER",
            arm=TREATMENT_ARM,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(assign, range(16)))

    assert len({result.assignment_id for result in results}) == 1
    assert all(result.persisted for result in results)
    assert {result.reason for result in results} <= {"ASSIGNED", "ALREADY_ASSIGNED"}
    assert journal._connection.execute(
        "SELECT COUNT(*) FROM experiment_assignments"
    ).fetchone()[0] == 1
    journal.close()


def test_worker_persists_decision_lifecycle_execution_and_outcome(tmp_path: Path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    definition = _definition()
    opportunity = _opportunity("full-lifecycle")

    def write():
        assignment = journal.assignment(
            definition, opportunity, mode="PAPER", arm=CONTROL_ARM,
        )
        assert journal.record_decision(
            assignment.assignment_id,
            control_decision="CURRENT_TRIGGER",
            treatment_decision="CONTROL_FALLBACK",
            selected_mode="CURRENT_TRIGGER",
            decision={"worker": True},
        )
        assert journal.link_lifecycle(assignment.assignment_id, "lifecycle-1")
        assert journal.record_execution_event(
            assignment.assignment_id,
            event_id="execution-1",
            event_type="ENTRY_SUBMITTED",
            observed_at=NOW.isoformat(),
            order_id="order-1",
        )
        assert journal.record_outcome(
            assignment.assignment_id,
            {"state": "REJECTED"},
            outcome_id="outcome-1",
            order_id="order-1",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(write).result()

    connection = journal._connection
    assert connection.execute("SELECT COUNT(*) FROM experiment_decisions").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM experiment_assignment_links").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM experiment_execution_events").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM experiment_outcomes").fetchone()[0] == 1
    journal.close()


def test_reopen_preserves_existing_assignment_and_accepts_new_one(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    first = PaperExperimentJournal(path)
    definition = _definition()
    original = first.assignment(
        definition, _opportunity("reopen-existing"), mode="PAPER", arm=CONTROL_ARM,
    )
    first.close()

    reopened = PaperExperimentJournal(path)
    existing = reopened.assignment(
        definition, _opportunity("reopen-existing"), mode="PAPER", arm=TREATMENT_ARM,
    )
    new = reopened.assignment(
        definition, _opportunity("reopen-new"), mode="PAPER", arm=TREATMENT_ARM,
    )
    assert existing.assignment_id == original.assignment_id
    assert existing.reason == "ALREADY_ASSIGNED"
    assert new.persisted and new.reason == "ASSIGNED"
    assert reopened._connection.execute(
        "SELECT COUNT(*) FROM experiment_assignments"
    ).fetchone()[0] == 2
    reopened.close()


def test_real_entry_policy_worker_thread_persists_ineligible_assignment(tmp_path: Path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(enabled=True, mode=PAPER_TREATMENT),
        journal=journal,
    )

    def assess():
        return policy.assess(
            _di_result(),
            _candidate(),
            environment="PAPER",
            signal_factory=lambda _candidate: object(),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        decision, signal = pool.submit(assess).result()

    assert signal is None
    assert decision.assignment_persisted
    assert decision.treatment_decision == "CONTROL_FALLBACK"
    assert journal._connection.execute(
        "SELECT COUNT(*) FROM experiment_assignments"
    ).fetchone()[0] == 1
    assert journal._connection.execute(
        "SELECT COUNT(*) FROM experiment_decisions"
    ).fetchone()[0] == 1
    policy.close()
    journal.close()


def test_real_desktop_callback_worker_thread_persists_assignment(tmp_path: Path, monkeypatch) -> None:
    from app.composition import create_desktop_composition
    import app.composition.desktop as desktop_module
    from app.configuration import load_configuration
    from app.strategies.warrior_momentum.forward_models import CaptureRecordType
    from tests.trade_intelligence.test_entry_timing import _result
    from tests.warrior_momentum.test_forward_capture import account, point

    configuration = load_configuration({
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED": "true",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE": "PAPER_TREATMENT",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH": str(tmp_path / "experiment.sqlite3"),
        "WARRIOR_FORWARD_PAPER_ENABLED": "true",
        "WARRIOR_FORWARD_CAPTURE_PATH": str(tmp_path / "forward.sqlite3"),
        "ALLOWED_SYMBOLS": "XYZ",
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    composition = create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
        paper_clock=lambda: NOW,
    )
    try:
        sidecar = composition.warrior_forward_sidecar
        assert sidecar is not None
        sidecar.start("PAPER")
        assert sidecar._service is not None
        sidecar._decision_intelligence_observer.observe_decision = lambda **kwargs: replace(
            _result(), setup_evidence=(), readiness_memberships=(),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            candidate, signal = pool.submit(
                sidecar._service.observe, point(), account=account(),
            ).result()
        assert candidate is not None
        # Signal presence belongs to the canonical strategy/control path and
        # may vary independently of experimental treatment eligibility.  This
        # test proves only the worker-thread persistence boundary.
        sidecar._writer.flush()
        journal = sidecar._paper_entry_intelligence._journal
        assert journal is not None
        assert journal._connection.execute(
            "SELECT COUNT(*) FROM experiment_assignments"
        ).fetchone()[0] == 1
        diagnostics = {
            record.payload_json for record in sidecar._store.records(
                record_type=CaptureRecordType.DI_ENTRY_DIAGNOSTIC,
            )
        }
        assert any("CALLBACK_ENTERED" in row for row in diagnostics)
        assert any("POLICY_ASSESS_CALLED" in row for row in diagnostics)
        assert any("POLICY_ASSESS_RETURNED" in row for row in diagnostics)
        assert any("ASSIGNMENT_PERSISTED" in row for row in diagnostics)
    finally:
        composition.close(timeout_seconds=1.0)

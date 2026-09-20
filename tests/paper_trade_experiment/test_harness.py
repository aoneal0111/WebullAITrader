from __future__ import annotations

import sqlite3

from app.paper_trade_experiment import (
    CONTROL_ARM,
    ExperimentDefinition,
    ExperimentOpportunity,
    ExperimentRouter,
    PaperExperimentJournal,
    TREATMENT_ARM,
    one_dimension_delta,
    wave_one_definitions,
)


def opportunity(index: int = 1, *, strategy: str = "TEST_STRATEGY") -> ExperimentOpportunity:
    return ExperimentOpportunity(
        assignment_identity=f"decision-{index}", strategy=strategy, symbol=f"T{index:03d}",
        trading_date="2026-09-14", decision_timestamp="2026-09-14T14:30:00+00:00",
        context={"market_regime": "MIXED", "session": "REGULAR"},
    )


def definition(*, enabled: bool = False, control_allocation: int = 50) -> ExperimentDefinition:
    return ExperimentDefinition(
        "test-experiment", "1", "TEST_STRATEGY",
        {"policy": "control", "research_only": True},
        {"policy": "treatment", "research_only": True},
        "2026-09-14T00:00:00+00:00", enabled=enabled,
        control_allocation_percent=control_allocation,
        treatment_allocation_percent=100 - control_allocation,
        minimum_sample_target=10, research_provenance="deterministic fixture",
    )


def test_wave_one_definitions_are_disabled_and_isolated() -> None:
    definitions = wave_one_definitions()
    assert len(definitions) == 3
    assert all(not item.enabled and item.paper_only for item in definitions)
    assert all(one_dimension_delta(item) == ("policy",) for item in definitions)


def test_disabled_router_preserves_control_and_persists_assignment(tmp_path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    try:
        result = ExperimentRouter(journal).assign(definition(), opportunity(), mode="PAPER")
        assert result.arm == CONTROL_ARM and result.fallback is False
        again = ExperimentRouter(journal).assign(definition(), opportunity(), mode="PAPER")
        assert again.assignment_id == result.assignment_id and again.arm == CONTROL_ARM
        assert journal.status()["experiments"] == []
    finally:
        journal.close()


def test_enabled_assignment_is_stable_balanced_and_restart_safe(tmp_path) -> None:
    path = tmp_path / "journal.sqlite3"
    journal = PaperExperimentJournal(path)
    journal.register(definition(enabled=True))
    router = ExperimentRouter(journal, enabled=True)
    results = [router.assign(definition(enabled=True), opportunity(i), mode="PAPER") for i in range(100)]
    assert {item.arm for item in results} == {CONTROL_ARM, TREATMENT_ARM}
    assert 35 <= sum(item.arm == TREATMENT_ARM for item in results) <= 65
    first = results[0]
    journal.close()
    reopened = PaperExperimentJournal(path)
    try:
        again = ExperimentRouter(reopened, enabled=True).assign(
            definition(enabled=True), opportunity(0), mode="PAPER"
        )
        assert (again.assignment_id, again.arm) == (first.assignment_id, first.arm)
        row = reopened._connection.execute("SELECT COUNT(*) FROM experiment_assignments").fetchone()
        assert row[0] == 100
    finally:
        reopened.close()


def test_assignment_identity_cannot_be_reassigned_by_definition_version(tmp_path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    try:
        original = definition(enabled=True)
        journal.register(original)
        first = ExperimentRouter(journal, enabled=True).assign(
            original, opportunity(7), mode="PAPER"
        )
        revised = ExperimentDefinition(
            "test-experiment", "2", "TEST_STRATEGY", original.control_arm,
            original.treatment_arm, original.start_timestamp, enabled=True,
        )
        replay = ExperimentRouter(journal, enabled=True).assign(
            revised, opportunity(7), mode="PAPER"
        )
        assert (replay.assignment_id, replay.arm, replay.reason) == (
            first.assignment_id, first.arm, "ALREADY_ASSIGNED"
        )
        assert journal._connection.execute(
            "SELECT COUNT(*) FROM experiment_assignments"
        ).fetchone()[0] == 1
    finally:
        journal.close()


def test_live_always_bypasses_treatment(tmp_path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    try:
        called = []
        result = ExperimentRouter(journal, enabled=True).route(
            definition(enabled=True), opportunity(), mode="LIVE", treatment=lambda: called.append(True)
        )
        assert result.assignment.arm == CONTROL_ARM
        assert not result.treatment_executed and not called
    finally:
        journal.close()


def test_assignment_is_durable_before_treatment_invocation(tmp_path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    try:
        seen = []
        def treatment() -> None:
            seen.append(journal._connection.execute("SELECT COUNT(*) FROM experiment_assignments").fetchone()[0])
        enabled_definition = definition(enabled=True, control_allocation=0)
        journal.register(enabled_definition)
        result = ExperimentRouter(journal, enabled=True).route(
            enabled_definition, opportunity(2), mode="PAPER", treatment=treatment
        )
        assert result.treatment_executed and seen == [1]
    finally:
        journal.close()


def test_persistence_failure_falls_back_to_control() -> None:
    class BrokenJournal:
        def assignment(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("sidecar unavailable")
    result = ExperimentRouter(BrokenJournal(), enabled=True).assign(
        definition(enabled=True), opportunity(), mode="PAPER"
    )
    assert result.arm == CONTROL_ARM and result.fallback and not result.persisted


def test_outcomes_are_idempotent_and_unfilled_assignment_remains_visible(tmp_path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    try:
        definition_value = definition()
        journal.register(definition_value)
        assignment = ExperimentRouter(journal).assign(definition_value, opportunity(), mode="PAPER")
        assert journal.record_outcome(assignment.assignment_id, {"state": "REJECTED", "R": 0}, outcome_id="event-1")
        assert not journal.record_outcome(assignment.assignment_id, {"state": "REJECTED", "R": 0}, outcome_id="event-1")
        report = journal.status()["experiments"][0]
        assert report["progress"][CONTROL_ARM] == {"assigned": 1, "completed": 1}
        assert report["metrics"][CONTROL_ARM]["r_mean"] == 0.0
        assert journal.record_outcome(
            assignment.assignment_id,
            {"pnl": "12.50", "MFE": "1.75", "MAE": "-0.25"},
            outcome_id="event-2",
        )
        metrics = journal.status()["experiments"][0]["metrics"][CONTROL_ARM]
        assert metrics["pnl_mean"] == 12.5
        assert metrics["mfe_mean"] == 1.75
        assert metrics["mae_mean"] == -0.25
    finally:
        journal.close()


def test_sidecar_status_exposes_progress(tmp_path) -> None:
    journal = PaperExperimentJournal(tmp_path / "journal.sqlite3")
    try:
        journal.register(definition())
        report = journal.status()
        assert report["framework_version"] == "ATLAS_PAPER_EXPERIMENTS_V1"
        item = report["experiments"][0]
        assert item["assigned_observations"] == 0 and item["percent_toward_target"] == 0
    finally:
        journal.close()

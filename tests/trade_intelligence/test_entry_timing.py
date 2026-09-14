from datetime import UTC, datetime
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

from app.strategies.warrior_momentum.models import SetupDetection, SetupState, SetupType, StopModel
from app.trade_intelligence.decision_intelligence.entry_timing import (
    EntryIntelligenceConfig, HistoricalPaperEntryTimingPolicy, PAPER_TREATMENT,
)
from app.trade_intelligence.decision_intelligence.models import HistoricalIntelligenceResult
from app.paper_trade_experiment.harness import PaperExperimentJournal


NOW = datetime(2026, 8, 10, 14, 35, tzinfo=UTC)


def _result(*, stage="TRIGGER_READY", location="APPROACHING_TRIGGER"):
    return HistoricalIntelligenceResult(
        opportunity_id="opportunity-1", trading_date="2026-08-10", evaluated_at=NOW,
        recognized_memberships=("HIGH_OF_DAY_BREAKOUT", "FLAT_TOP_BREAKOUT"),
        primary_strategy="HIGH_OF_DAY_BREAKOUT", setup_stage=stage,
        entry_location=location, entry_assessment="FAVORABLE_LOCATION",
        trigger_price=Decimal("10"), structural_stop=Decimal("9.80"),
        current_price=Decimal("9.95"), setup_evidence=(
            {"confidence_state": "STRONG_EVIDENCE", "test_sample_count": 100,
             "walk_forward_state": "STABLE"},
        ),
        readiness_memberships=(
            {"strategy": "HIGH_OF_DAY_BREAKOUT", "state": "TRIGGER_ARMED"},
        ),
    )


def _candidate():
    setup = SetupDetection(
        SetupType.HIGH_OF_DAY_BREAKOUT, SetupState.FORMING, Decimal("80"),
        Decimal("10"), Decimal("9.80"), StopModel.BREAKOUT_LEVEL,
    )
    return SimpleNamespace(symbol="XYZ", setup=setup)


def test_disabled_policy_is_observation_only_and_does_not_create_signal(tmp_path: Path):
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(), journal=journal,
    )
    called = []
    decision, signal = policy.assess(
        _result(), _candidate(), environment="PAPER",
        signal_factory=lambda candidate: called.append(candidate) or object(),
    )
    assert decision.arm == "CONTROL"
    assert signal is None
    assert called == []
    journal.close()


def test_armed_result_without_candidate_setup_still_persists_assignment(tmp_path: Path):
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(enabled=True, mode=PAPER_TREATMENT), journal=journal,
    )
    candidate = SimpleNamespace(symbol="XYZ", setup=None)
    insufficient = replace(_result(), setup_evidence=())
    decision, signal = policy.assess(
        insufficient, candidate, environment="PAPER", signal_factory=lambda _candidate: None,
    )
    assert signal is None
    assert decision.treatment_decision == "CONTROL_FALLBACK"
    assert journal._connection.execute("SELECT COUNT(*) FROM experiment_assignments").fetchone()[0] == 1
    assert journal._connection.execute("SELECT COUNT(*) FROM experiment_decisions").fetchone()[0] == 1
    payload = journal._connection.execute(
        "SELECT decision_json FROM experiment_decisions"
    ).fetchone()[0]
    assert "NO_DI_EVIDENCE" in json.loads(payload)["blocking_reasons"]
    journal.close()


def test_live_always_bypasses_treatment(tmp_path: Path):
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(enabled=True, mode=PAPER_TREATMENT), journal=journal,
    )
    called = []
    decision, signal = policy.assess(
        _result(), _candidate(), environment="LIVE",
        signal_factory=lambda candidate: called.append(candidate) or object(),
    )
    assert decision.arm == "CONTROL"
    assert signal is None
    assert called == []
    journal.close()


def test_trigger_ready_treatment_uses_existing_signal_factory_and_structural_stop(tmp_path: Path):
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(enabled=True, mode=PAPER_TREATMENT, allocation_percent=0), journal=journal,
    )
    captured = []
    decision, signal = policy.assess(
        _result(), _candidate(), environment="PAPER",
        signal_factory=lambda candidate: captured.append(candidate) or "normal-signal",
    )
    assert decision.treatment_decision == "ELIGIBLE"
    assert signal == "normal-signal"
    assert len(captured) == 1
    assert captured[0].setup.state is SetupState.TRIGGERED
    assert captured[0].setup.stop_price == Decimal("9.80")
    journal.close()


def test_extended_or_weak_evidence_falls_back_to_control(tmp_path: Path):
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(enabled=True, mode=PAPER_TREATMENT), journal=journal,
    )
    decision, signal = policy.assess(
        _result(location="HEAVILY_EXTENDED"), _candidate(), environment="PAPER",
        signal_factory=lambda candidate: object(),
    )
    assert decision.entry_quality.state == "CHASE_RISK"
    assert signal is None
    journal.close()


def test_readiness_is_scoped_to_the_candidate_membership(tmp_path: Path):
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(enabled=True, mode=PAPER_TREATMENT, allocation_percent=0),
        journal=journal,
    )
    unsupported = _candidate()
    unsupported.setup = SetupDetection(
        SetupType.MICRO_PULLBACK, SetupState.FORMING, Decimal("80"),
        Decimal("10"), Decimal("9.80"), StopModel.MICRO_PULLBACK_LOW,
        taxonomy_strategy_id="FIRST_PULLBACK",
    )
    result = _result()
    result = replace(result, setup_evidence=(
        {"strategy": "HIGH_OF_DAY_BREAKOUT", "confidence_state": "STRONG_EVIDENCE", "test_sample_count": 100,
         "walk_forward_state": "STABLE"},
        {"strategy": "FIRST_PULLBACK", "confidence_state": "STRONG_EVIDENCE", "test_sample_count": 100,
         "walk_forward_state": "STABLE"},
    ))
    decision, signal = policy.assess(
        result, unsupported, environment="PAPER", signal_factory=lambda _candidate: object(),
    )
    assert signal is None
    assert decision.treatment_decision == "CONTROL_FALLBACK"
    assert decision.authorizing_memberships == ()
    journal.close()


def test_control_signal_is_linked_for_shadow_order_and_fill_attribution(tmp_path: Path):
    journal = PaperExperimentJournal(tmp_path / "experiment.sqlite3")
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(enabled=True, mode=PAPER_TREATMENT,
                                       allocation_percent=100),
        journal=journal,
    )
    control_signal = SimpleNamespace(lifecycle_id="paper-lifecycle-1")
    decision, signal = policy.assess(
        _result(), _candidate(), environment="PAPER",
        signal_factory=lambda candidate: object(),
        existing_signal=control_signal,
    )
    assert decision.arm == "CONTROL"
    assert signal is None
    assignment = journal._connection.execute(
        "SELECT assignment_id FROM experiment_assignments"
    ).fetchone()[0]
    assert journal.assignment_for_lifecycle("paper-lifecycle-1")["assignment_id"] == assignment
    shadow_types = {
        row[0] for row in journal._connection.execute(
            "SELECT shadow_type FROM experiment_shadows WHERE assignment_id=?",
            (assignment,),
        )
    }
    assert shadow_types == {"CONTROL_TRIGGER", "CONTROL_DECISION"}
    journal.close()

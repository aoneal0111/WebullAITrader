from __future__ import annotations

from dataclasses import replace
from dataclasses import asdict
from datetime import timedelta
import sqlite3

import pytest

from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.models import (
    AtlasDecision, DecisionObservation, PaperExecutionObservation, canonical_json,
)
from app.trade_intelligence.service import TradeIntelligenceService
from tests.trade_intelligence.conftest import make_experience


def observation(experience, *, seconds=0, decision=AtlasDecision.REJECTED):
    at = experience.snapshot.decision_timestamp + timedelta(seconds=seconds)
    snapshot = replace(
        experience.snapshot, decision_timestamp=at, source_timestamp=at,
        setup_timestamp=at,
        feature_source_timestamps=tuple(
            (name, at) for name, _ in experience.snapshot.feature_source_timestamps
        ),
    )
    return DecisionObservation(
        experience_id=experience.experience_id, observed_at=at,
        source_event_identity=f"decision:{seconds}", atlas_decision=decision,
        snapshot=snapshot, blockers=("SPREAD_WIDE",) if seconds else experience.blockers,
        technically_actionable=True, lifecycle_stage=decision.value,
    )


def test_decision_history_is_append_only_and_idempotent(tmp_path, experience):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    store.put_experience(experience)
    first = observation(experience)
    second = observation(experience, seconds=3, decision=AtlasDecision.ENTRY_READY)
    assert store.put_decision_observation(first)
    assert not store.put_decision_observation(first)
    assert store.put_decision_observation(second)
    assert store.get_experience(experience.experience_id) == experience
    assert store.decision_observations(experience.experience_id) == (first, second)


def test_decision_cannot_reference_missing_experience(tmp_path, experience):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError, match="does not exist"):
        store.put_decision_observation(observation(experience))


def test_service_drains_experience_and_decision_on_orderly_shutdown(tmp_path, experience):
    path = tmp_path / "memory.sqlite3"
    service = TradeIntelligenceService(path, capacity=8)
    assert service.submit_experience(experience)
    assert service.submit_decision(observation(experience))
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.accepted == metrics.completed
    assert metrics.outstanding == 0
    store = ExperienceStore(path)
    assert len(store.decision_observations(experience.experience_id)) == 1


def test_child_admitted_before_parent_is_bounded_and_completed(tmp_path, experience):
    path = tmp_path / "memory.sqlite3"
    service = TradeIntelligenceService(path, capacity=8)
    child = observation(experience)
    assert service.submit_decision(child)
    assert service.submit_experience(experience)
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.accepted == 2
    assert metrics.completed == 2
    assert metrics.failed == metrics.outstanding == 0
    assert ExperienceStore(path).decision_observations(experience.experience_id) == (child,)


def test_restart_replays_checkpointed_parent_before_child(tmp_path, experience):
    path = tmp_path / "memory.sqlite3"
    child = observation(experience)
    store = ExperienceStore(path)
    assert store.checkpoint_work(
        child.decision_id, "DECISION", child.observed_at, canonical_json(asdict(child)),
    )
    assert store.checkpoint_work(
        experience.experience_id, "EXPERIENCE", experience.snapshot.decision_timestamp,
        canonical_json(asdict(experience)),
    )
    service = TradeIntelligenceService(path, capacity=8)
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.completed == 2
    assert metrics.failed == metrics.outstanding == 0
    assert len(ExperienceStore(path).decision_observations(experience.experience_id)) == 1


def test_restart_with_completed_parent_replays_checkpointed_child(tmp_path, experience):
    path = tmp_path / "memory.sqlite3"
    child = observation(experience)
    store = ExperienceStore(path)
    assert store.put_experience(experience)
    assert store.checkpoint_work(
        child.decision_id, "DECISION", child.observed_at, canonical_json(asdict(child)),
    )
    service = TradeIntelligenceService(path, capacity=8)
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.completed == 1
    assert metrics.failed == metrics.outstanding == 0
    assert len(ExperienceStore(path).decision_observations(experience.experience_id)) == 1


def test_missing_parent_fails_explicitly_only_at_terminal_drain(tmp_path, experience):
    path = tmp_path / "memory.sqlite3"
    service = TradeIntelligenceService(path, capacity=2)
    assert service.submit_decision(observation(experience))
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.failed == 1
    assert metrics.outstanding == 0
    with sqlite3.connect(path) as db:
        error = db.execute(
            "SELECT error FROM work_ledger WHERE work_type='DECISION'"
        ).fetchone()[0]
    assert '"prerequisite_related":true' in error
    assert '"retryable":false' in error


def test_replacement_episode_keeps_each_queued_child_with_its_parent(tmp_path, experience):
    path = tmp_path / "memory.sqlite3"
    replacement = make_experience(symbol=experience.key.symbol, episode="replacement")
    first_child = observation(experience)
    replacement_child = observation(replacement, seconds=1)
    service = TradeIntelligenceService(path, capacity=8)
    assert service.submit_experience(experience)
    assert service.submit_decision(first_child)
    assert service.submit_experience(replacement)
    assert service.submit_decision(replacement_child)
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.completed == 4
    assert metrics.failed == metrics.outstanding == 0
    store = ExperienceStore(path)
    assert store.decision_observations(experience.experience_id) == (first_child,)
    assert store.decision_observations(replacement.experience_id) == (replacement_child,)


def test_duplicate_parent_admission_does_not_lose_dependent_decision(tmp_path, experience):
    path = tmp_path / "memory.sqlite3"
    child = observation(experience)
    service = TradeIntelligenceService(path, capacity=8)
    assert service.submit_experience(experience)
    assert service.submit_experience(experience)
    assert service.submit_decision(child)
    assert service.close(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.accepted == metrics.completed == 2
    assert metrics.suppressed_duplicate == 1
    assert metrics.failed == metrics.outstanding == 0


def test_correlated_paper_fill_is_an_actual_trade_fact(tmp_path, experience):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    store.put_experience(experience)
    store.put_paper_execution_observation(PaperExecutionObservation(
        observation_id="paper:fill:1", observed_at=experience.snapshot.decision_timestamp,
        event_type="ORDER_FILLED", symbol=experience.key.symbol,
        experience_id=experience.experience_id, correlation_status="CORRELATED",
        order_id="order-1", fill_id="fill-1",
    ))
    assert store.has_actual_paper_execution(experience.experience_id)
    report = store.aggregate_report()
    assert report["actually_traded"] == 1
    assert report["classifications"]["NOT_APPLICABLE"] == 1

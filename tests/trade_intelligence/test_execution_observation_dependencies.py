from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
import json
import sqlite3
from threading import Event

import pytest

from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.models import PaperExecutionObservation, canonical_json
from app.trade_intelligence.service import TradeIntelligenceService
from tests.trade_intelligence.conftest import make_experience


def _paper(experience, event_type: str, sequence: int) -> PaperExecutionObservation:
    return PaperExecutionObservation(
        observation_id=f"desktop-paper-execution:{sequence}:{event_type}",
        observed_at=experience.snapshot.decision_timestamp,
        event_type=event_type,
        symbol=experience.key.symbol,
        experience_id=experience.experience_id,
        correlation_status="CORRELATED",
        order_id="PAPER-TEST-ORDER",
        side="BUY",
        quantity=100,
        strategy_lifecycle_id="WARRIOR_MOMENTUM_V1|XYZ|test",
    )


def _ledger(path, work_id: str):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM work_ledger WHERE work_id=?", (work_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def test_correlated_order_observations_wait_for_delayed_experience_parent(tmp_path):
    path = tmp_path / "memory.sqlite3"
    experience = make_experience(symbol="TLYS", episode="dynamic-paper-entry")
    accepted = _paper(experience, "ORDER_ACCEPTED", 1)
    working = _paper(experience, "ORDER_WORKING", 2)

    service = TradeIntelligenceService(path, capacity=8)
    assert service.observe_paper_execution(accepted)
    assert service.observe_paper_execution(working)
    assert service.submit_experience(experience)
    assert service.close(timeout_seconds=10)

    metrics = service.metrics()
    assert metrics.completed == 3
    assert metrics.failed == metrics.outstanding == 0
    with sqlite3.connect(path) as connection:
        persisted = connection.execute(
            "SELECT observation_id,event_type FROM paper_execution_observations "
            "WHERE experience_id=? ORDER BY observed_at,observation_id",
            (experience.experience_id,),
        ).fetchall()
    assert persisted == [
        (accepted.observation_id, accepted.event_type),
        (working.observation_id, working.event_type),
    ]
    assert _ledger(path, accepted.observation_id)["attempt_count"] == 1
    assert _ledger(path, working.observation_id)["attempt_count"] == 1


def test_delayed_parent_is_durably_dependency_deferred_then_resumed_once(tmp_path):
    path = tmp_path / "memory.sqlite3"
    experience = make_experience(symbol="TLYS", episode="delayed-parent")
    child = _paper(experience, "ORDER_ACCEPTED", 1)
    parent_started = Event()
    release_parent = Event()

    class BlockingParentStore(ExperienceStore):
        def put_experience(self, value):
            parent_started.set()
            assert release_parent.wait(10)
            return super().put_experience(value)

    service = TradeIntelligenceService(
        path, capacity=8, store_factory=BlockingParentStore,
    )
    assert service.observe_paper_execution(child)
    assert service.submit_experience(experience)
    assert parent_started.wait(10)
    deferred = _ledger(path, child.observation_id)
    assert deferred["state"] == "DEPENDENCY_DEFERRED"
    assert deferred["dependency_type"] == "EXPERIENCE"
    assert deferred["dependency_id"] == experience.experience_id
    assert deferred["deferred_count"] == 1
    assert deferred["attempt_count"] == 0
    assert deferred["error"] is None

    release_parent.set()
    assert service.close(timeout_seconds=10)
    completed = _ledger(path, child.observation_id)
    assert completed["state"] == "COMPLETED"
    assert completed["attempt_count"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_execution_observations WHERE observation_id=?",
            (child.observation_id,),
        ).fetchone()[0] == 1


def test_restart_replays_deferred_paper_child_after_checkpointed_parent(tmp_path):
    path = tmp_path / "memory.sqlite3"
    experience = make_experience(symbol="TLYS", episode="restart-parent")
    child = _paper(experience, "ORDER_WORKING", 2)
    store = ExperienceStore(path)
    assert store.checkpoint_work(
        child.observation_id, "PAPER_OBSERVATION", child.observed_at,
        canonical_json(asdict(child)),
    )
    store.defer_work(
        child.observation_id, child.observed_at,
        dependency_type="EXPERIENCE", dependency_id=experience.experience_id,
    )
    assert store.checkpoint_work(
        experience.experience_id, "EXPERIENCE",
        experience.snapshot.decision_timestamp, canonical_json(asdict(experience)),
    )

    service = TradeIntelligenceService(path, capacity=8)
    assert service.close(timeout_seconds=10)
    assert service.metrics().failed == service.metrics().outstanding == 0
    assert _ledger(path, child.observation_id)["state"] == "COMPLETED"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM paper_execution_observations WHERE observation_id=?",
            (child.observation_id,),
        ).fetchone()[0] == 1


def test_uncorrelated_paper_execution_does_not_require_research_parent(tmp_path):
    path = tmp_path / "memory.sqlite3"
    observation = PaperExecutionObservation(
        observation_id="paper:unmatched:accepted",
        observed_at=datetime(2026, 9, 3, 13, 0, tzinfo=UTC),
        event_type="ORDER_ACCEPTED", symbol="NOCTX",
        correlation_status="UNRESOLVED", order_id="PAPER-NOCTX",
        side="BUY", quantity=10,
    )
    service = TradeIntelligenceService(path, capacity=8)
    assert service.observe_paper_execution(observation)
    assert service.close(timeout_seconds=10)
    assert service.metrics().completed == 1
    assert service.metrics().failed == 0
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT experience_id,correlation_status FROM paper_execution_observations"
        ).fetchone()
    assert row == (None, "UNRESOLVED")


def test_missing_correlated_parent_is_terminal_only_at_final_drain(tmp_path):
    path = tmp_path / "memory.sqlite3"
    experience = make_experience(symbol="TLYS", episode="invalid-parent")
    child = _paper(experience, "ORDER_ACCEPTED", 1)
    service = TradeIntelligenceService(path, capacity=8)
    assert service.observe_paper_execution(child)
    assert service.close(timeout_seconds=10)

    ledger = _ledger(path, child.observation_id)
    error = json.loads(ledger["error"])
    assert ledger["state"] == "FAILED"
    assert ledger["deferred_count"] == 1
    assert ledger["attempt_count"] == 0
    assert error["error_class"] == "MissingPrerequisiteError"
    assert error["prerequisite_related"] is True
    assert error["data_lost"] is True
    assert error["retryable"] is False


def test_full_order_lifecycle_preserves_event_order_and_fill_fact(tmp_path):
    path = tmp_path / "memory.sqlite3"
    experience = make_experience(symbol="TLYS", episode="full-order-lifecycle")
    event_types = (
        "ORDER_ACCEPTED", "ORDER_WORKING", "ORDER_PARTIALLY_FILLED",
        "ORDER_FILLED", "ORDER_CANCELLED",
    )
    service = TradeIntelligenceService(path, capacity=16)
    assert service.submit_experience(experience)
    for sequence, event_type in enumerate(event_types, 1):
        assert service.observe_paper_execution(_paper(experience, event_type, sequence))
    assert service.close(timeout_seconds=10)
    assert service.metrics().failed == service.metrics().outstanding == 0
    with sqlite3.connect(path) as connection:
        persisted = tuple(row[0] for row in connection.execute(
            "SELECT event_type FROM paper_execution_observations ORDER BY observed_at,observation_id"
        ))
    assert persisted == event_types
    assert ExperienceStore(path).has_actual_paper_execution(experience.experience_id)


def test_duplicate_child_is_idempotent_and_conflicting_content_is_terminal(tmp_path):
    path = tmp_path / "memory.sqlite3"
    experience = make_experience(symbol="TLYS", episode="immutable-child")
    child = _paper(experience, "ORDER_ACCEPTED", 1)
    store = ExperienceStore(path)
    assert store.put_experience(experience)
    assert store.put_paper_execution_observation(child)
    assert not store.put_paper_execution_observation(child)
    with pytest.raises(ValueError, match="conflicting content"):
        store.put_paper_execution_observation(replace(child, event_type="ORDER_WORKING"))


def test_non_dependency_sqlite_failure_is_not_reclassified_as_parent_lag(tmp_path):
    path = tmp_path / "memory.sqlite3"
    experience = make_experience(symbol="TLYS", episode="commit-failure")
    child = _paper(experience, "ORDER_ACCEPTED", 1)

    class CommitFailingStore(ExperienceStore):
        def put_paper_execution_observation(self, value):
            raise sqlite3.OperationalError("injected commit failure")

    service = TradeIntelligenceService(
        path, capacity=8, store_factory=CommitFailingStore,
    )
    assert service.submit_experience(experience)
    assert service.observe_paper_execution(child)
    assert service.close(timeout_seconds=10)
    error = json.loads(_ledger(path, child.observation_id)["error"])
    assert error["error_class"] == "OperationalError"
    assert error["prerequisite_related"] is False
    assert error["retryable"] is False


def test_v3_store_migrates_dependency_ledger_without_rewriting_facts(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES('schema_version','3')")
        connection.execute(
            "CREATE TABLE work_ledger(work_id TEXT PRIMARY KEY,work_type TEXT NOT NULL,"
            "state TEXT NOT NULL,accepted_at TEXT NOT NULL,payload_json TEXT NOT NULL,"
            "started_at TEXT,completed_at TEXT,error TEXT)"
        )
        connection.execute(
            "INSERT INTO work_ledger VALUES('old','BAR','COMPLETED','2026-01-01T00:00:00+00:00',"
            "'{}',NULL,'2026-01-01T00:00:01+00:00',NULL)"
        )
    ExperienceStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0] == "4"
        row = connection.execute(
            "SELECT state,attempt_count,deferred_count FROM work_ledger WHERE work_id='old'"
        ).fetchone()
    assert row == ("COMPLETED", 0, 0)

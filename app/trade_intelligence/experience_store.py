"""Versioned SQLite store owned exclusively by Trade Intelligence research."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import (
    ActualPaperExecutionOutcome, AtlasDecision, DecisionObservation, DecisionTimeSnapshot,
    DatasetPartition, ExperienceSource, HorizonOutcome, OpportunityKey,
    OutcomeKind, OutcomeStatus, PriceBar, ResearchGeneration,
    PaperExecutionObservation, ResearchGenerationCompletion, TradeOpportunityExperience,
    canonical_json, experience_payload,
    decision_analog_signature,
)

STORE_SCHEMA_VERSION = 2


class ExperienceStore:
    """Durable, idempotent store with immutable decision-time rows.

    The constructor creates/opens only the explicitly supplied research path.
    It has no discovery logic for production databases.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiences(
                    experience_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    feature_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    session TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    setup_type TEXT,
                    setup_state TEXT,
                    catalyst_status TEXT NOT NULL,
                    atlas_decision TEXT NOT NULL,
                    technically_actionable INTEGER NOT NULL,
                    actually_traded INTEGER NOT NULL,
                    partition_name TEXT NOT NULL,
                    decision_timestamp TEXT NOT NULL,
                    blockers_json TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    analog_signature TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    source_store TEXT,
                    source_record_identity TEXT,
                    UNIQUE(source_store, source_record_identity));
                CREATE INDEX IF NOT EXISTS ix_experience_cohorts ON experiences(
                    session_date,session,symbol,setup_type,atlas_decision,catalyst_status);
                CREATE INDEX IF NOT EXISTS ix_experience_analogs ON experiences(
                    analog_signature,decision_timestamp);
                CREATE TABLE IF NOT EXISTS outcomes(
                    experience_id TEXT NOT NULL REFERENCES experiences(experience_id),
                    horizon_minutes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    target_timestamp TEXT NOT NULL,
                    reached_1r INTEGER,reached_2r INTEGER,stop_reached INTEGER,
                    first_plan_event TEXT,mfe_r TEXT,mae_r TEXT,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    PRIMARY KEY(experience_id,horizon_minutes));
                CREATE TABLE IF NOT EXISTS future_bars(
                    symbol TEXT NOT NULL,bar_timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(symbol,bar_timestamp));
                CREATE TABLE IF NOT EXISTS work_ledger(
                    work_id TEXT PRIMARY KEY,work_type TEXT NOT NULL,
                    state TEXT NOT NULL,accepted_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,started_at TEXT,completed_at TEXT,error TEXT);
                CREATE TABLE IF NOT EXISTS actual_paper_outcomes(
                    execution_record_identity TEXT PRIMARY KEY,
                    experience_id TEXT NOT NULL REFERENCES experiences(experience_id),
                    payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experience_decisions(
                    decision_id TEXT PRIMARY KEY,
                    experience_id TEXT NOT NULL REFERENCES experiences(experience_id),
                    observed_at TEXT NOT NULL,atlas_decision TEXT NOT NULL,
                    lifecycle_stage TEXT NOT NULL,blockers_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,payload_digest TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_experience_decisions_episode
                    ON experience_decisions(experience_id,observed_at,decision_id);
                CREATE TRIGGER IF NOT EXISTS experience_decision_immutable_update
                    BEFORE UPDATE ON experience_decisions BEGIN
                    SELECT RAISE(ABORT,'decision observation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS experience_decision_immutable_delete
                    BEFORE DELETE ON experience_decisions BEGIN
                    SELECT RAISE(ABORT,'decision observation is immutable'); END;
                CREATE TABLE IF NOT EXISTS paper_execution_observations(
                    observation_id TEXT PRIMARY KEY,
                    experience_id TEXT REFERENCES experiences(experience_id),
                    observed_at TEXT NOT NULL,event_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,correlation_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,payload_digest TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_paper_observations_episode
                    ON paper_execution_observations(experience_id,observed_at);
                CREATE TRIGGER IF NOT EXISTS paper_observation_immutable_update
                    BEFORE UPDATE ON paper_execution_observations BEGIN
                    SELECT RAISE(ABORT,'PAPER observation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS paper_observation_immutable_delete
                    BEFORE DELETE ON paper_execution_observations BEGIN
                    SELECT RAISE(ABORT,'PAPER observation is immutable'); END;
                CREATE TABLE IF NOT EXISTS import_ledger(
                    source_store TEXT NOT NULL,source_schema_version TEXT NOT NULL,
                    import_version TEXT NOT NULL,source_record_identity TEXT NOT NULL,
                    experience_id TEXT NOT NULL,
                    PRIMARY KEY(source_store,source_schema_version,import_version,source_record_identity));
                CREATE TABLE IF NOT EXISTS research_models(
                    model_id TEXT PRIMARY KEY,generation_id TEXT NOT NULL
                    REFERENCES research_generations(generation_id),
                    model_version TEXT NOT NULL,feature_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN (
                        'CHALLENGER','HOLDOUT_VALIDATED','SHADOW_VALIDATED',
                        'PAPER_CHALLENGER','ELIGIBLE_FOR_PROMOTION','REJECTED')),
                    execution_capability TEXT NOT NULL CHECK(execution_capability='NON_EXECUTABLE'),
                    specification_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_evaluations(
                    evaluation_id TEXT PRIMARY KEY,model_id TEXT NOT NULL
                    REFERENCES research_models(model_id),partition_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,completed_at TEXT,
                    metrics_json TEXT NOT NULL,evidence_cutoff TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_generations(
                    generation_id TEXT PRIMARY KEY,
                    partition_policy_version TEXT NOT NULL,
                    training_start TEXT NOT NULL,training_end TEXT NOT NULL,
                    validation_start TEXT NOT NULL,validation_end TEXT NOT NULL,
                    holdout_start TEXT NOT NULL,holdout_end TEXT NOT NULL,
                    evidence_cutoff TEXT NOT NULL,feature_version TEXT NOT NULL,
                    experience_schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,model_version TEXT,policy_version TEXT,
                    predecessor_generation_id TEXT REFERENCES research_generations(generation_id),
                    payload_json TEXT NOT NULL,payload_digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_generation_completions(
                    generation_id TEXT PRIMARY KEY REFERENCES research_generations(generation_id),
                    completed_at TEXT NOT NULL,evaluation_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS research_generation_assignments(
                    generation_id TEXT NOT NULL REFERENCES research_generations(generation_id),
                    experience_id TEXT NOT NULL REFERENCES experiences(experience_id),
                    session_date TEXT NOT NULL,partition_name TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY(generation_id,experience_id));
                CREATE TRIGGER IF NOT EXISTS research_generation_immutable_update
                    BEFORE UPDATE ON research_generations BEGIN
                    SELECT RAISE(ABORT,'research generation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS research_generation_immutable_delete
                    BEFORE DELETE ON research_generations BEGIN
                    SELECT RAISE(ABORT,'research generation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS research_assignment_immutable_update
                    BEFORE UPDATE ON research_generation_assignments BEGIN
                    SELECT RAISE(ABORT,'generation assignment is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS research_assignment_immutable_delete
                    BEFORE DELETE ON research_generation_assignments BEGIN
                    SELECT RAISE(ABORT,'generation assignment is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS research_completion_immutable_update
                    BEFORE UPDATE ON research_generation_completions BEGIN
                    SELECT RAISE(ABORT,'generation completion is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS research_completion_immutable_delete
                    BEFORE DELETE ON research_generation_completions BEGIN
                    SELECT RAISE(ABORT,'generation completion is immutable'); END;
                CREATE TABLE IF NOT EXISTS admission_accounting(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    suppressed_duplicate INTEGER NOT NULL,
                    rejected INTEGER NOT NULL,pressure_episodes INTEGER NOT NULL);
                INSERT OR IGNORE INTO admission_accounting VALUES(1,0,0,0);
            """)
            row = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if row is None:
                db.execute("INSERT INTO metadata VALUES('schema_version',?)", (str(STORE_SCHEMA_VERSION),))
            elif int(row[0]) == 1:
                # V2 is append-only research history. Existing V1 experience and
                # outcome rows retain their exact meaning and payload digests.
                db.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (str(STORE_SCHEMA_VERSION),))
            elif int(row[0]) != STORE_SCHEMA_VERSION:
                raise ValueError("incompatible Trade Intelligence store schema")

    def put_experience(self, value: TradeOpportunityExperience) -> bool:
        payload = experience_payload(value)
        digest = _digest(payload)
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_digest FROM experiences WHERE experience_id=?",
                (value.experience_id,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise ValueError("immutable experience identity has conflicting content")
                return False
            db.execute(
                """INSERT INTO experiences(
                   experience_id,schema_version,feature_version,symbol,
                   session_date,session,environment,setup_type,setup_state,
                   catalyst_status,atlas_decision,technically_actionable,
                   actually_traded,partition_name,decision_timestamp,
                   blockers_json,features_json,analog_signature,payload_json,payload_digest,
                   source_store,source_record_identity
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value.experience_id, value.schema_version, value.feature_version,
                    value.key.symbol.upper(), value.key.session_date.isoformat(),
                    value.key.session.upper(), value.environment.upper(),
                    value.snapshot.setup_type, value.snapshot.setup_state,
                    value.snapshot.catalyst_status, value.atlas_decision.value,
                    value.technically_actionable, value.actually_traded,
                    value.partition.value, value.snapshot.decision_timestamp.isoformat(),
                    canonical_json(value.blockers), canonical_json(dict(value.snapshot.features)),
                    decision_analog_signature(value), payload, digest,
                    value.source_store, value.source_record_identity,
                ),
            )
            if value.source is ExperienceSource.EXTERNAL_SNAPSHOT_IMPORT:
                db.execute(
                    "INSERT INTO import_ledger VALUES(?,?,?,?,?)",
                    (value.source_store, value.source_schema_version, value.import_version,
                     value.source_record_identity, value.experience_id),
                )
            return True

    def put_experiences(self, values: Iterable[TradeOpportunityExperience]) -> tuple[int, int]:
        inserted = duplicate = 0
        with self._connect() as db:
            for value in values:
                payload = experience_payload(value)
                digest = _digest(payload)
                cursor = db.execute(
                    """INSERT OR IGNORE INTO experiences(
                       experience_id,schema_version,feature_version,symbol,
                       session_date,session,environment,setup_type,setup_state,
                       catalyst_status,atlas_decision,technically_actionable,
                       actually_traded,partition_name,decision_timestamp,
                       blockers_json,features_json,analog_signature,payload_json,
                       payload_digest,source_store,source_record_identity)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        value.experience_id, value.schema_version, value.feature_version,
                        value.key.symbol.upper(), value.key.session_date.isoformat(),
                        value.key.session.upper(), value.environment.upper(),
                        value.snapshot.setup_type, value.snapshot.setup_state,
                        value.snapshot.catalyst_status, value.atlas_decision.value,
                        value.technically_actionable, value.actually_traded,
                        value.partition.value, value.snapshot.decision_timestamp.isoformat(),
                        canonical_json(value.blockers), canonical_json(dict(value.snapshot.features)),
                        decision_analog_signature(value), payload, digest,
                        value.source_store, value.source_record_identity,
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    row = db.execute(
                        "SELECT payload_digest FROM experiences WHERE experience_id=?",
                        (value.experience_id,),
                    ).fetchone()
                    if row is None or row[0] != digest:
                        raise ValueError("immutable experience identity has conflicting content")
                    duplicate += 1
        return inserted, duplicate

    def get_experience(self, experience_id: str) -> TradeOpportunityExperience | None:
        with self._connect() as db:
            row = db.execute("SELECT payload_json FROM experiences WHERE experience_id=?", (experience_id,)).fetchone()
        return None if row is None else _experience_from_json(row[0])

    def experiences(self) -> tuple[TradeOpportunityExperience, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT payload_json FROM experiences ORDER BY decision_timestamp,experience_id").fetchall()
        return tuple(_experience_from_json(row[0]) for row in rows)

    def put_outcome(self, value: HorizonOutcome) -> bool:
        payload = canonical_json(asdict(value))
        digest = _digest(payload)
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_digest FROM outcomes WHERE experience_id=? AND horizon_minutes=?",
                (value.experience_id, value.horizon_minutes),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise ValueError("immutable horizon outcome has conflicting content")
                return False
            db.execute(
                """INSERT INTO outcomes(
                   experience_id,horizon_minutes,status,target_timestamp,
                   reached_1r,reached_2r,stop_reached,first_plan_event,mfe_r,mae_r,
                   payload_json,payload_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (value.experience_id, value.horizon_minutes, value.status.value,
                 value.target_timestamp.isoformat(), value.reached_1r,
                 value.reached_2r, value.stop_reached, value.first_plan_event,
                 None if value.mfe_r is None else str(value.mfe_r),
                 None if value.mae_r is None else str(value.mae_r), payload, digest),
            )
        return True

    def put_outcomes(self, values: Iterable[HorizonOutcome]) -> tuple[int, int]:
        inserted = duplicate = 0
        with self._connect() as db:
            for value in values:
                payload = canonical_json(asdict(value))
                digest = _digest(payload)
                cursor = db.execute(
                    """INSERT OR IGNORE INTO outcomes(
                       experience_id,horizon_minutes,status,target_timestamp,
                       reached_1r,reached_2r,stop_reached,first_plan_event,mfe_r,mae_r,
                       payload_json,payload_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (value.experience_id, value.horizon_minutes, value.status.value,
                     value.target_timestamp.isoformat(), value.reached_1r,
                     value.reached_2r, value.stop_reached, value.first_plan_event,
                     None if value.mfe_r is None else str(value.mfe_r),
                     None if value.mae_r is None else str(value.mae_r), payload, digest),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    row = db.execute(
                        "SELECT payload_digest FROM outcomes WHERE experience_id=? AND horizon_minutes=?",
                        (value.experience_id, value.horizon_minutes),
                    ).fetchone()
                    if row is None or row[0] != digest:
                        raise ValueError("immutable horizon outcome has conflicting content")
                    duplicate += 1
        return inserted, duplicate

    def analog_experiences(
        self, signature: str, as_of: datetime, limit: int,
    ) -> tuple[TradeOpportunityExperience, ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT payload_json FROM experiences
                   WHERE analog_signature=? AND decision_timestamp<?
                   ORDER BY decision_timestamp DESC,experience_id LIMIT ?""",
                (signature, as_of.isoformat(), limit),
            ).fetchall()
        return tuple(_experience_from_json(row[0]) for row in rows)

    def aggregate_report(self) -> dict[str, object]:
        """History-scaled cohort report using indexed/scalar SQL projections."""
        with self._connect() as db:
            def grouped(column: str) -> dict[str, int]:
                return dict(db.execute(
                    f"SELECT COALESCE({column},'UNAVAILABLE'),COUNT(*) FROM experiences GROUP BY {column} ORDER BY 1"
                ).fetchall())

            unique = int(db.execute("SELECT COUNT(*) FROM experiences").fetchone()[0])
            blockers = dict(db.execute("""
                SELECT value,COUNT(*) FROM experiences,json_each(blockers_json)
                GROUP BY value ORDER BY value
            """).fetchall())
            complete = dict(db.execute("""
                SELECT horizon_minutes,COUNT(*) FROM outcomes
                WHERE status='COMPLETE' GROUP BY horizon_minutes
            """).fetchall())
            counts = db.execute("""
                SELECT
                  SUM(e.actually_traded=1 OR EXISTS(
                    SELECT 1 FROM paper_execution_observations p
                    WHERE p.experience_id=e.experience_id
                      AND p.event_type IN ('ORDER_FILLED','ORDER_PARTIALLY_FILLED'))),
                  SUM(e.technically_actionable=1 OR EXISTS(
                    SELECT 1 FROM experience_decisions d
                    WHERE d.experience_id=e.experience_id
                      AND json_extract(d.payload_json,'$.technically_actionable')=1)),
                  SUM(e.atlas_decision='REJECTED' OR EXISTS(
                    SELECT 1 FROM experience_decisions d
                    WHERE d.experience_id=e.experience_id
                      AND d.atlas_decision='REJECTED'))
                FROM experiences e
            """).fetchone()
            classifications = dict(db.execute("""
                WITH latest AS (
                  SELECT o.* FROM outcomes o JOIN (
                    SELECT experience_id,MAX(horizon_minutes) horizon
                    FROM outcomes WHERE status='COMPLETE' GROUP BY experience_id
                  ) m ON m.experience_id=o.experience_id AND m.horizon=o.horizon_minutes
                ), facts AS (
                  SELECT e.*,
                    (e.actually_traded=1 OR EXISTS(
                      SELECT 1 FROM paper_execution_observations p
                      WHERE p.experience_id=e.experience_id
                        AND p.event_type IN ('ORDER_FILLED','ORDER_PARTIALLY_FILLED'))) traded,
                    (e.technically_actionable=1 OR EXISTS(
                      SELECT 1 FROM experience_decisions d
                      WHERE d.experience_id=e.experience_id
                        AND json_extract(d.payload_json,'$.technically_actionable')=1)) actionable
                  FROM experiences e
                ), classified AS (
                  SELECT CASE
                    WHEN e.traded=1 THEN 'NOT_APPLICABLE'
                    WHEN l.experience_id IS NULL OR e.actionable=0
                      THEN 'INSUFFICIENT_OUTCOME_DATA'
                    WHEN l.reached_2r=1 AND COALESCE(l.first_plan_event,'')!='STOP'
                      THEN 'PROFITABLE_MISSED_OPPORTUNITY'
                    WHEN l.first_plan_event='STOP' AND l.reached_1r=0
                      THEN 'PROTECTED_REJECTION'
                    WHEN l.reached_1r=1 AND l.stop_reached=1 AND l.reached_2r=0
                      THEN 'DANGEROUS_FALSE_POSITIVE'
                    ELSE 'NEUTRAL_REJECTION' END classification
                  FROM facts e LEFT JOIN latest l USING(experience_id)
                ) SELECT classification,COUNT(*) FROM classified GROUP BY classification
            """).fetchall())
            cohort_rows = db.execute("""
                WITH latest AS (
                  SELECT o.* FROM outcomes o JOIN (
                    SELECT experience_id,MAX(horizon_minutes) horizon
                    FROM outcomes WHERE status='COMPLETE' GROUP BY experience_id
                  ) m ON m.experience_id=o.experience_id AND m.horizon=o.horizon_minutes
                )
                SELECT j.value,l.reached_1r,l.reached_2r,
                       CASE WHEN l.first_plan_event='STOP' THEN 1 ELSE 0 END,
                       l.mfe_r,l.mae_r
                FROM experiences e JOIN json_each(e.blockers_json) j
                LEFT JOIN latest l USING(experience_id)
                ORDER BY j.value
            """).fetchall()
            cohorts: dict[str, list[tuple[object, ...]]] = {}
            for row in cohort_rows:
                cohorts.setdefault(row[0], []).append(tuple(row[1:]))
            return {
                "unique_experiences": unique,
                "by_symbol": grouped("symbol"), "by_date": grouped("session_date"),
                "by_session": grouped("session"), "by_setup_type": grouped("setup_type"),
                "by_atlas_decision": grouped("atlas_decision"), "by_blocker": blockers,
                "by_catalyst_state": grouped("catalyst_status"),
                "complete_horizons": {str(value): int(complete.get(value, 0)) for value in (1, 2, 5, 10, 15, 30)},
                "actually_traded": int(counts[0] or 0),
                "technically_actionable": int(counts[1] or 0),
                "rejected": int(counts[2] or 0),
                "classifications": classifications,
                "blocker_cohort_rows": cohorts,
            }

    def outcomes(self, experience_id: str | None = None) -> tuple[HorizonOutcome, ...]:
        sql = "SELECT payload_json FROM outcomes"
        args = ()
        if experience_id is not None:
            sql += " WHERE experience_id=?"
            args = (experience_id,)
        sql += " ORDER BY experience_id,horizon_minutes"
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        return tuple(_outcome_from_json(row[0]) for row in rows)

    def outcomes_for_experiences(self, experience_ids: tuple[str, ...]) -> dict[str, tuple[HorizonOutcome, ...]]:
        if not experience_ids:
            return {}
        placeholders = ",".join("?" for _ in experience_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT experience_id,payload_json FROM outcomes
                    WHERE experience_id IN ({placeholders})
                    ORDER BY experience_id,horizon_minutes""",
                experience_ids,
            ).fetchall()
        grouped: dict[str, list[HorizonOutcome]] = {identity: [] for identity in experience_ids}
        for row in rows:
            grouped[row[0]].append(_outcome_from_json(row[1]))
        return {key: tuple(value) for key, value in grouped.items()}

    def checkpoint_and_size_bytes(self) -> int:
        with self._connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return sum(
            item.stat().st_size for item in (
                self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")
            ) if item.exists()
        )

    def incomplete_experiences(self) -> tuple[TradeOpportunityExperience, ...]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT e.payload_json FROM experiences e
                LEFT JOIN outcomes o ON o.experience_id=e.experience_id
                GROUP BY e.experience_id HAVING COUNT(o.horizon_minutes)<6
                ORDER BY e.decision_timestamp
            """).fetchall()
        return tuple(_experience_from_json(row[0]) for row in rows)

    def put_bar(self, bar: PriceBar) -> bool:
        payload = canonical_json(asdict(bar))
        with self._connect() as db:
            existing = db.execute(
                "SELECT payload_json FROM future_bars WHERE symbol=? AND bar_timestamp=?",
                (bar.symbol.upper(), bar.timestamp.isoformat()),
            ).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise ValueError(
                        "immutable completed bar identity has conflicting content"
                    )
                return False
            cursor = db.execute(
                "INSERT OR IGNORE INTO future_bars VALUES(?,?,?)",
                (bar.symbol.upper(), bar.timestamp.isoformat(), payload),
            )
            return cursor.rowcount == 1

    def bars(self, symbol: str) -> tuple[PriceBar, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload_json FROM future_bars WHERE symbol=? ORDER BY bar_timestamp",
                (symbol.upper(),),
            ).fetchall()
        return tuple(_bar_from_json(row[0]) for row in rows)

    def prune_bars(self) -> None:
        """Keep only bars needed by at least one incomplete experience."""
        with self._connect() as db:
            oldest = db.execute("""
                SELECT MIN(e.decision_timestamp) FROM experiences e
                WHERE (SELECT COUNT(*) FROM outcomes o WHERE o.experience_id=e.experience_id)<6
            """).fetchone()[0]
            if oldest is None:
                db.execute("DELETE FROM future_bars")
            else:
                db.execute("DELETE FROM future_bars WHERE bar_timestamp < ?", (oldest,))

    def checkpoint_work(self, work_id: str, work_type: str, accepted_at: datetime, payload_json: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO work_ledger VALUES(?,?,'CHECKPOINTED',?,?,NULL,NULL,NULL)",
                (work_id, work_type, accepted_at.isoformat(), payload_json),
            )
            return cursor.rowcount == 1

    def start_work(self, work_id: str, timestamp: datetime) -> None:
        with self._connect() as db:
            db.execute("UPDATE work_ledger SET state='STARTED',started_at=? WHERE work_id=? AND state='CHECKPOINTED'", (timestamp.isoformat(), work_id))

    def complete_work(self, work_id: str, timestamp: datetime, error: str | None = None) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE work_ledger SET state=?,completed_at=?,error=? WHERE work_id=?",
                ("FAILED" if error else "COMPLETED", timestamp.isoformat(), error, work_id),
            )

    def work_state(self, work_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT state FROM work_ledger WHERE work_id=?", (work_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def accounting(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute("SELECT state,COUNT(*) FROM work_ledger GROUP BY state").fetchall()
        values = {row[0]: row[1] for row in rows}
        with self._connect() as db:
            admission = db.execute(
                "SELECT suppressed_duplicate,rejected,pressure_episodes FROM admission_accounting WHERE id=1"
            ).fetchone()
        return {
            "accepted": sum(values.values()),
            "checkpointed": values.get("CHECKPOINTED", 0),
            "started": values.get("STARTED", 0),
            "completed": values.get("COMPLETED", 0),
            "failed": values.get("FAILED", 0),
            "outstanding": values.get("CHECKPOINTED", 0) + values.get("STARTED", 0),
            "suppressed_duplicate": admission[0],
            "rejected": admission[1],
            "pressure_episodes": admission[2],
        }

    def record_admission_accounting(self, *, suppressed: int, rejected: int, pressure_episodes: int) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE admission_accounting SET
                   suppressed_duplicate=suppressed_duplicate+?,
                   rejected=rejected+?,pressure_episodes=pressure_episodes+?
                   WHERE id=1""",
                (suppressed, rejected, pressure_episodes),
            )

    def recover_started_work(self) -> int:
        with self._connect() as db:
            cursor = db.execute("UPDATE work_ledger SET state='CHECKPOINTED',started_at=NULL WHERE state='STARTED'")
            return cursor.rowcount

    def recoverable_work(self) -> tuple[tuple[str, str, str, datetime], ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT work_id,work_type,payload_json,accepted_at
                   FROM work_ledger WHERE state IN ('CHECKPOINTED','STARTED')
                   ORDER BY CASE work_type WHEN 'EXPERIENCE' THEN 0 ELSE 1 END,
                            accepted_at,work_id"""
            ).fetchall()
        return tuple((row[0], row[1], row[2], datetime.fromisoformat(row[3])) for row in rows)

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM experiences").fetchone()[0])

    def put_actual_paper_outcome(self, value: ActualPaperExecutionOutcome) -> bool:
        payload = canonical_json(asdict(value))
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO actual_paper_outcomes VALUES(?,?,?)",
                (value.execution_record_identity, value.experience_id, payload),
            )
            return cursor.rowcount == 1

    def put_decision_observation(self, value: DecisionObservation) -> bool:
        payload = canonical_json(asdict(value))
        digest = _digest(payload)
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_digest FROM experience_decisions WHERE decision_id=?",
                (value.decision_id,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise ValueError("immutable decision identity has conflicting content")
                return False
            if db.execute("SELECT 1 FROM experiences WHERE experience_id=?", (value.experience_id,)).fetchone() is None:
                raise ValueError("decision experience does not exist")
            db.execute(
                "INSERT INTO experience_decisions VALUES(?,?,?,?,?,?,?,?)",
                (value.decision_id, value.experience_id, value.observed_at.isoformat(),
                 value.atlas_decision.value, value.lifecycle_stage,
                 canonical_json(value.blockers), payload, digest),
            )
            return True

    def decision_observations(self, experience_id: str) -> tuple[DecisionObservation, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload_json FROM experience_decisions WHERE experience_id=? ORDER BY observed_at,decision_id",
                (experience_id,),
            ).fetchall()
        return tuple(_decision_from_json(row[0]) for row in rows)

    def put_paper_execution_observation(self, value: PaperExecutionObservation) -> bool:
        payload = canonical_json(asdict(value))
        digest = _digest(payload)
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_digest FROM paper_execution_observations WHERE observation_id=?",
                (value.observation_id,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise ValueError("immutable PAPER observation identity has conflicting content")
                return False
            db.execute(
                "INSERT INTO paper_execution_observations VALUES(?,?,?,?,?,?,?,?)",
                (value.observation_id, value.experience_id, value.observed_at.isoformat(),
                 value.event_type, value.symbol.upper(), value.correlation_status,
                 payload, digest),
            )
            return True

    def has_actual_paper_execution(self, experience_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                """SELECT 1 FROM paper_execution_observations
                   WHERE experience_id=? AND event_type IN (
                     'ORDER_FILLED','ORDER_PARTIALLY_FILLED') LIMIT 1""",
                (experience_id,),
            ).fetchone()
        return row is not None

    def put_research_generation(self, value: ResearchGeneration) -> bool:
        payload = canonical_json(asdict(value))
        digest = _digest(payload)
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_digest FROM research_generations WHERE generation_id=?",
                (value.generation_id,),
            ).fetchone()
            if row is not None:
                if row[0] != digest:
                    raise ValueError("immutable research generation has conflicting definition")
                return False
            if value.predecessor_generation_id is not None:
                predecessor = db.execute(
                    """SELECT holdout_start,holdout_end FROM research_generations
                       WHERE generation_id=?""",
                    (value.predecessor_generation_id,),
                ).fetchone()
                if predecessor is None:
                    raise ValueError("predecessor generation does not exist")
                previous_holdout_start = date.fromisoformat(predecessor[0])
                previous_holdout_end = date.fromisoformat(predecessor[1])
                incorporates_holdout = (
                    _ranges_overlap(value.training_start, value.training_end,
                                    previous_holdout_start, previous_holdout_end)
                    or _ranges_overlap(value.validation_start, value.validation_end,
                                       previous_holdout_start, previous_holdout_end)
                )
                if incorporates_holdout:
                    completed = db.execute(
                        "SELECT 1 FROM research_generation_completions WHERE generation_id=?",
                        (value.predecessor_generation_id,),
                    ).fetchone()
                    if completed is None:
                        raise ValueError("predecessor holdout cannot be reused before frozen completion")
                if value.holdout_start <= previous_holdout_end:
                    raise ValueError("next generation must reserve a later untouched holdout")
            db.execute(
                """INSERT INTO research_generations VALUES(
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    value.generation_id, value.partition_policy_version,
                    value.training_start.isoformat(), value.training_end.isoformat(),
                    value.validation_start.isoformat(), value.validation_end.isoformat(),
                    value.holdout_start.isoformat(), value.holdout_end.isoformat(),
                    value.evidence_cutoff.isoformat(), value.feature_version,
                    value.experience_schema_version, value.created_at.isoformat(),
                    value.model_version, value.policy_version,
                    value.predecessor_generation_id, payload, digest,
                ),
            )
            return True

    def complete_research_generation(self, value: ResearchGenerationCompletion) -> bool:
        payload = canonical_json(asdict(value))
        with self._connect() as db:
            row = db.execute(
                "SELECT payload_json FROM research_generation_completions WHERE generation_id=?",
                (value.generation_id,),
            ).fetchone()
            if row is not None:
                if row[0] != payload:
                    raise ValueError("generation completion is immutable")
                return False
            generation = db.execute("SELECT created_at FROM research_generations WHERE generation_id=?", (value.generation_id,)).fetchone()
            if generation is None:
                raise ValueError("research generation does not exist")
            if value.completed_at < datetime.fromisoformat(generation[0]):
                raise ValueError("generation completion cannot precede creation")
            db.execute(
                "INSERT INTO research_generation_completions VALUES(?,?,?,?)",
                (value.generation_id, value.completed_at.isoformat(),
                 value.evaluation_digest, payload),
            )
            return True

    def assign_experience_to_generation(
        self, generation_id: str, experience_id: str, *, assigned_at: datetime,
    ) -> DatasetPartition:
        if assigned_at.tzinfo is None:
            raise ValueError("assignment timestamp must be timezone-aware")
        with self._connect() as db:
            generation_row = db.execute(
                "SELECT payload_json FROM research_generations WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            experience_row = db.execute(
                "SELECT session_date FROM experiences WHERE experience_id=?",
                (experience_id,),
            ).fetchone()
            if generation_row is None or experience_row is None:
                raise ValueError("generation and experience must exist")
            generation = _generation_from_json(generation_row[0])
            session_date = date.fromisoformat(experience_row[0])
            if session_date > generation.evidence_cutoff:
                raise ValueError("future experience exceeds frozen generation evidence cutoff")
            partition = generation.partition_for(session_date)
            existing = db.execute(
                """SELECT partition_name FROM research_generation_assignments
                   WHERE generation_id=? AND experience_id=?""",
                (generation_id, experience_id),
            ).fetchone()
            if existing is not None:
                if existing[0] != partition.value:
                    raise ValueError("historical generation assignment conflict")
                return partition
            db.execute(
                "INSERT INTO research_generation_assignments VALUES(?,?,?,?,?)",
                (generation_id, experience_id, session_date.isoformat(),
                 partition.value, assigned_at.isoformat()),
            )
            return partition

    def generation_assignments(self, generation_id: str) -> tuple[tuple[str, date, DatasetPartition], ...]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT experience_id,session_date,partition_name
                   FROM research_generation_assignments WHERE generation_id=?
                   ORDER BY session_date,experience_id""",
                (generation_id,),
            ).fetchall()
        return tuple((row[0], date.fromisoformat(row[1]), DatasetPartition(row[2])) for row in rows)


def _digest(payload: str) -> str:
    from hashlib import sha256
    return sha256(payload.encode()).hexdigest()


def _experience_from_json(payload: str) -> TradeOpportunityExperience:
    item = json.loads(payload)
    key = item["key"]
    snapshot = item["snapshot"]
    datetime_fields = ("decision_timestamp", "source_timestamp", "setup_timestamp")
    for name in datetime_fields:
        if snapshot.get(name) is not None:
            snapshot[name] = datetime.fromisoformat(snapshot[name])
    snapshot["feature_source_timestamps"] = tuple(
        (name, datetime.fromisoformat(value)) for name, value in snapshot["feature_source_timestamps"]
    )
    snapshot["features"] = tuple((name, _decimal_feature(value)) for name, value in snapshot["features"])
    for name in (
        "last_price", "bid", "ask", "spread_percent", "percentage_change",
        "current_volume", "average_volume", "relative_volume", "dollar_volume",
        "float_shares", "quote_freshness_seconds", "trade_freshness_seconds",
        "scanner_score", "setup_quality", "trigger_price", "structural_stop",
        "reference_price", "risk_per_share",
    ):
        if snapshot.get(name) is not None:
            snapshot[name] = Decimal(snapshot[name])
    snapshot["passed_rules"] = tuple(snapshot["passed_rules"])
    snapshot["failed_rules"] = tuple(snapshot["failed_rules"])
    return TradeOpportunityExperience(
        key=OpportunityKey(key["strategy_id"], key["symbol"], date.fromisoformat(key["session_date"]), key["session"], key["episode_id"]),
        environment=item["environment"], policy_version=item["policy_version"],
        strategy_version=item["strategy_version"], model_version=item["model_version"],
        feature_version=item["feature_version"], source_event_identity=item["source_event_identity"],
        snapshot=DecisionTimeSnapshot(**snapshot), atlas_decision=AtlasDecision(item["atlas_decision"]),
        blockers=tuple(item["blockers"]), technically_actionable=item["technically_actionable"],
        actually_traded=item["actually_traded"], source=ExperienceSource(item["source"]),
        source_store=item["source_store"], source_schema_version=item["source_schema_version"],
        import_version=item["import_version"], source_record_identity=item["source_record_identity"],
        schema_version=item["schema_version"],
    )


def _outcome_from_json(payload: str) -> HorizonOutcome:
    item = json.loads(payload)
    item["target_timestamp"] = datetime.fromisoformat(item["target_timestamp"])
    item["outcome_as_of"] = None if item["outcome_as_of"] is None else datetime.fromisoformat(item["outcome_as_of"])
    item["status"] = OutcomeStatus(item["status"])
    item["technical_outcome_kind"] = OutcomeKind(item["technical_outcome_kind"])
    item["plan_outcome_kind"] = None if item["plan_outcome_kind"] is None else OutcomeKind(item["plan_outcome_kind"])
    for name in ("future_price", "return_percent", "mfe", "mae", "mfe_r", "mae_r"):
        if item[name] is not None:
            item[name] = Decimal(item[name])
    return HorizonOutcome(**item)


def _decision_from_json(payload: str) -> DecisionObservation:
    item = json.loads(payload)
    snapshot = item["snapshot"]
    for name in ("decision_timestamp", "source_timestamp", "setup_timestamp"):
        if snapshot.get(name) is not None:
            snapshot[name] = datetime.fromisoformat(snapshot[name])
    snapshot["feature_source_timestamps"] = tuple(
        (name, datetime.fromisoformat(value)) for name, value in snapshot["feature_source_timestamps"]
    )
    snapshot["features"] = tuple((name, _decimal_feature(value)) for name, value in snapshot["features"])
    for name in (
        "last_price", "bid", "ask", "spread_percent", "percentage_change",
        "current_volume", "average_volume", "relative_volume", "dollar_volume",
        "float_shares", "quote_freshness_seconds", "trade_freshness_seconds",
        "scanner_score", "setup_quality", "trigger_price", "structural_stop",
        "reference_price", "risk_per_share",
    ):
        if snapshot.get(name) is not None:
            snapshot[name] = Decimal(snapshot[name])
    snapshot["passed_rules"] = tuple(snapshot["passed_rules"])
    snapshot["failed_rules"] = tuple(snapshot["failed_rules"])
    item["snapshot"] = DecisionTimeSnapshot(**snapshot)
    item["observed_at"] = datetime.fromisoformat(item["observed_at"])
    item["atlas_decision"] = AtlasDecision(item["atlas_decision"])
    item["blockers"] = tuple(item["blockers"])
    return DecisionObservation(**item)


def _paper_observation_from_json(payload: str) -> PaperExecutionObservation:
    item = json.loads(payload)
    item["observed_at"] = datetime.fromisoformat(item["observed_at"])
    for name in ("price", "quantity"):
        if item[name] is not None:
            item[name] = Decimal(item[name])
    return PaperExecutionObservation(**item)


def _bar_from_json(payload: str) -> PriceBar:
    item = json.loads(payload)
    item["timestamp"] = datetime.fromisoformat(item["timestamp"])
    for name in ("open", "high", "low", "close", "volume"):
        item[name] = Decimal(item[name])
    return PriceBar(**item)


def _generation_from_json(payload: str) -> ResearchGeneration:
    item = json.loads(payload)
    for name in (
        "training_start", "training_end", "validation_start", "validation_end",
        "holdout_start", "holdout_end", "evidence_cutoff",
    ):
        item[name] = date.fromisoformat(item[name])
    item["created_at"] = datetime.fromisoformat(item["created_at"])
    return ResearchGeneration(**item)


def _decimal_feature(value):
    # JSON string represents Decimal features; enum/string categorical features
    # are preserved. Current V1 numeric feature strings always parse as Decimal.
    if not isinstance(value, str):
        return value
    try:
        return Decimal(value)
    except Exception:
        return value


def _ranges_overlap(left_start: date, left_end: date, right_start: date, right_end: date) -> bool:
    return left_start <= right_end and right_start <= left_end

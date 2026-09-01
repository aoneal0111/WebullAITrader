"""Durable, restart-safe Atlas paper trade experiment journal."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from app.momentum_scanner import ScannerDecision
from app.paper_trade_experiment.models import (
    CandidateRecord,
    ExecutionState,
    HORIZONS_SECONDS,
)


SCHEMA_VERSION = "atlas-paper-experiment-v1"
INCREMENTAL_ENGINE_VERSION = "2"
RESEARCH_WORK_SCHEMA_VERSION = "atlas-research-work-v2"
DEFAULT_STRATEGY_VERSION = "momentum-scanner-v1"
DEFAULT_MODEL_VERSION = "none"
_SAFE_EXECUTION_ENVIRONMENTS = frozenset({"PAPER", "TEST"})
_SECRET_KEY_PARTS = (
    "api_key", "api_secret", "password", "credential", "access_token",
    "refresh_token", "authorization", "account_id",
)


@dataclass(frozen=True, slots=True)
class PreparedResearchWork:
    """Canonical, durable input for one research decision update."""

    work_id: str
    payload_json: str
    enqueued_at: datetime

    @property
    def payload(self) -> Mapping[str, Any]:
        return json.loads(self.payload_json)


def prepare_research_work(
    decision: ScannerDecision,
    *,
    execution_environment: str,
    strategy_version: str = DEFAULT_STRATEGY_VERSION,
    model_version: str = DEFAULT_MODEL_VERSION,
    enqueued_at: datetime | None = None,
    create_candidate: bool = True,
    logical_identity: Mapping[str, Any] | None = None,
) -> PreparedResearchWork:
    """Build one durable observation, optionally creating one candidate.

    A work item always carries the immutable market observation.  Candidate
    features are present only for the first event in a logical decision
    episode.  This is deliberately bounded and performs no SQLite work.
    """

    if decision.timestamp is None or decision.price is None:
        raise ValueError("complete scanner decision is required")
    from app.live_scanner.session import scanner_session

    environment = _safe_environment(execution_environment)
    features = None
    candidate_id = None
    if create_candidate:
        features = _decision_features(
            decision,
            market_session=scanner_session(decision.timestamp).value,
            scanner_rank=decision.scanner_rank,
            strategy_version=strategy_version,
            model_version=model_version,
            application_commit=_safe_application_commit(),
            execution_environment=environment,
        )
        if logical_identity is not None:
            canonical_identity = json.loads(_json(logical_identity))
            features["logical_candidate_identity"] = canonical_identity
            candidate_id = _logical_candidate_id(canonical_identity)
        else:
            candidate_id = _candidate_id(features)
    payload_json = _json({
        "work_schema": RESEARCH_WORK_SCHEMA_VERSION,
        "observation": {
            "symbol": decision.symbol.strip().upper(),
            "price": str(decision.price),
            "timestamp": (
                decision.last_price_timestamp or decision.timestamp
            ).astimezone(UTC).isoformat(),
        },
        "candidate_id": candidate_id,
        "candidate_features": features if create_candidate else None,
    })
    work_id = "research-work-" + hashlib.sha256(
        payload_json.encode("utf-8")
    ).hexdigest()
    accepted_at = enqueued_at or datetime.now(UTC)
    if accepted_at.tzinfo is None:
        raise ValueError("research enqueue timestamp must be timezone-aware")
    return PreparedResearchWork(
        work_id, payload_json, accepted_at.astimezone(UTC)
    )


def prepare_price_observation(
    symbol: str,
    timestamp: datetime,
    price: Decimal,
    *,
    enqueued_at: datetime | None = None,
) -> PreparedResearchWork:
    """Build a durable observation-only envelope without scanner features."""

    normalized = symbol.strip().upper()
    observed = Decimal(price)
    if not normalized or timestamp.tzinfo is None:
        raise ValueError("symbol and timezone-aware timestamp are required")
    if not observed.is_finite() or observed <= 0:
        raise ValueError("observed price must be positive and finite")
    payload_json = _json({
        "work_schema": RESEARCH_WORK_SCHEMA_VERSION,
        "observation": {
            "symbol": normalized,
            "price": str(observed),
            "timestamp": timestamp.astimezone(UTC).isoformat(),
        },
        "candidate_id": None,
        "candidate_features": None,
    })
    work_id = "research-work-" + hashlib.sha256(
        payload_json.encode("utf-8")
    ).hexdigest()
    accepted_at = enqueued_at or datetime.now(UTC)
    if accepted_at.tzinfo is None:
        raise ValueError("research enqueue timestamp must be timezone-aware")
    return PreparedResearchWork(
        work_id, payload_json, accepted_at.astimezone(UTC)
    )


class PaperTradeExperimentJournal:
    """SQLite journal with immutable decision features and mutable labels.

    The class has no broker dependency and cannot submit an order.  Execution
    mutations are accepted only for explicit PAPER/TEST records with live
    trading disabled.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._last_price_observation: dict[str, tuple[datetime, Decimal]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = self._new_connection()
        self._initialize()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.create_function("research_mfe", 3, _research_mfe)
        connection.create_function("research_mae", 3, _research_mae)
        return connection

    def _connect(self) -> sqlite3.Connection:
        """Return the journal's single-thread-owned long-lived connection."""

        return self._connection

    def close(self) -> None:
        self._connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiment_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    trade_id TEXT UNIQUE,
                    symbol TEXT NOT NULL,
                    decision_timestamp TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    execution_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS experiment_candidates_symbol_time
                    ON experiment_candidates(symbol, decision_timestamp);
                CREATE TABLE IF NOT EXISTS experiment_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_active_candidates (
                    candidate_id TEXT PRIMARY KEY REFERENCES experiment_candidates(candidate_id)
                        ON DELETE CASCADE,
                    symbol TEXT NOT NULL,
                    decision_timestamp TEXT NOT NULL,
                    reference_price TEXT NOT NULL,
                    next_due_horizon TEXT,
                    next_due_at TEXT,
                    completed_horizons_json TEXT NOT NULL,
                    mfe_so_far TEXT NOT NULL,
                    mae_so_far TEXT NOT NULL,
                    last_observation_time TEXT,
                    counterfactual_pending INTEGER NOT NULL CHECK(counterfactual_pending IN (0,1)),
                    execution_active INTEGER NOT NULL CHECK(execution_active IN (0,1)),
                    extrema_initialized INTEGER NOT NULL CHECK(extrema_initialized IN (0,1))
                );
                CREATE INDEX IF NOT EXISTS research_active_symbol_time
                    ON research_active_candidates(symbol, decision_timestamp);
                CREATE INDEX IF NOT EXISTS research_active_due
                    ON research_active_candidates(symbol, next_due_at);
                CREATE TABLE IF NOT EXISTS research_work_items (
                    work_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    enqueued_at TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('CHECKPOINTED','STARTED','COMPLETED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    completed_at TEXT,
                    error_type TEXT
                );
                CREATE INDEX IF NOT EXISTS research_work_recovery
                    ON research_work_items(state, enqueued_at, work_id);
                CREATE TABLE IF NOT EXISTS research_worker_telemetry (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    items_rejected INTEGER NOT NULL,
                    queue_high_water INTEGER NOT NULL,
                    lag_max_ms REAL NOT NULL,
                    resumed INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            existing = connection.execute(
                "SELECT value FROM experiment_metadata WHERE key='schema_version'"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO experiment_metadata(key,value) VALUES(?,?)",
                    ("schema_version", SCHEMA_VERSION),
                )
            elif existing["value"] != SCHEMA_VERSION:
                raise ValueError("unsupported paper experiment journal schema")
            engine = connection.execute(
                "SELECT value FROM experiment_metadata "
                "WHERE key='incremental_engine_version'"
            ).fetchone()
            if engine is None:
                self._bootstrap_incremental_state(connection)
                connection.execute(
                    "INSERT INTO experiment_metadata(key,value) VALUES(?,?)",
                    ("incremental_engine_version", INCREMENTAL_ENGINE_VERSION),
                )
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if result is None or result[0] != "ok":
                    raise ValueError("paper experiment journal integrity check failed")
            elif engine["value"] != INCREMENTAL_ENGINE_VERSION:
                if engine["value"] == "1":
                    # Version 2 changes the durable work envelope and recovery
                    # policy, not the candidate or horizon table layouts.
                    connection.execute(
                        "UPDATE experiment_metadata SET value=? "
                        "WHERE key='incremental_engine_version'",
                        (INCREMENTAL_ENGINE_VERSION,),
                    )
                else:
                    raise ValueError("unsupported incremental research engine schema")

    def _bootstrap_incremental_state(self, connection: sqlite3.Connection) -> None:
        """One-time deterministic migration of legacy pending/open rows."""

        rows = connection.execute(
            """SELECT * FROM experiment_candidates
               WHERE COALESCE(json_extract(labels_json,'$.outcome_status'),'PENDING')
                     != 'COMPLETE'
                  OR json_extract(execution_json,'$.state')
                     IN ('PARTIALLY_FILLED','FILLED')
               ORDER BY candidate_id"""
        ).fetchall()
        for row in rows:
            labels = json.loads(row["labels_json"])
            execution = json.loads(row["execution_json"])
            self._sync_active_row(connection, row, labels, execution)

    def record_candidate(
        self,
        decision: ScannerDecision,
        *,
        market_session: str = "UNKNOWN",
        scanner_rank: int | None = None,
        strategy_version: str = DEFAULT_STRATEGY_VERSION,
        model_version: str = DEFAULT_MODEL_VERSION,
        application_commit: str | None = None,
        execution_environment: str = "PAPER",
        recorded_at: datetime | None = None,
    ) -> CandidateRecord:
        if decision.timestamp is None or decision.price is None:
            raise ValueError("complete scanner decision timestamp and price are required")
        environment = _safe_environment(execution_environment)
        features = _decision_features(
            decision,
            market_session=market_session,
            scanner_rank=scanner_rank,
            strategy_version=strategy_version,
            model_version=model_version,
            application_commit=application_commit or _safe_application_commit(),
            execution_environment=environment,
        )
        _assert_no_secrets(features)
        candidate_id = _candidate_id(features)
        now = (recorded_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        execution = {
            "state": ExecutionState.NOT_EXECUTED.value,
            "paper_trade_executed": False,
            "fill_ids": [],
            "fill_quantity": "0",
        }
        encoded_features = _json(features)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT features_json FROM experiment_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if row is not None:
                if row["features_json"] != encoded_features:
                    raise ValueError("immutable candidate decision snapshot mismatch")
                return self.get(candidate_id)
            connection.execute(
                """INSERT INTO experiment_candidates(
                    candidate_id,trade_id,symbol,decision_timestamp,features_json,
                    labels_json,execution_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_id, None, decision.symbol,
                    decision.timestamp.astimezone(UTC).isoformat(),
                    encoded_features, _json({}), _json(execution), now, now,
                ),
            )
            inserted = connection.execute(
                "SELECT * FROM experiment_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            assert inserted is not None
            self._sync_active_row(connection, inserted, {}, execution)
        return self.get(candidate_id)

    def record_scanner_decision(
        self,
        decision: ScannerDecision,
        *,
        execution_environment: str = "PAPER",
        strategy_version: str = DEFAULT_STRATEGY_VERSION,
        model_version: str = DEFAULT_MODEL_VERSION,
    ) -> CandidateRecord:
        """Label older opportunities, then persist this immutable observation."""

        work = prepare_research_work(
            decision,
            execution_environment=execution_environment,
            strategy_version=strategy_version,
            model_version=model_version,
        )
        return self.process_prepared_work(work)

    def checkpoint_work_items(
        self, items: Iterable[PreparedResearchWork]
    ) -> tuple[int, int]:
        inserted = duplicates = 0
        with self._connect() as connection:
            for item in items:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO research_work_items(
                       work_id,payload_json,enqueued_at,state
                       ) VALUES(?,?,?,'CHECKPOINTED')""",
                    (item.work_id, item.payload_json, item.enqueued_at.isoformat()),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    duplicates += 1
        return inserted, duplicates

    def recoverable_work_items(self) -> tuple[PreparedResearchWork, ...]:
        """Return only v2 work whose semantics can be resumed exactly.

        Legacy v1 rows combined a tick observation with an unconditional
        tick-scale candidate creation.  They remain CHECKPOINTED in place for
        audit/recovery review and are intentionally not replayed into the v2
        logical-candidate model.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT work_id,payload_json,enqueued_at
                   FROM research_work_items
                   WHERE state IN ('CHECKPOINTED','STARTED')
                     AND json_valid(payload_json)=1
                     AND json_extract(payload_json,'$.work_schema')=?
                   ORDER BY enqueued_at,work_id""",
                (RESEARCH_WORK_SCHEMA_VERSION,),
            ).fetchall()
            connection.execute(
                "UPDATE research_work_items SET state='CHECKPOINTED' "
                "WHERE state='STARTED' AND json_valid(payload_json)=1 "
                "AND json_extract(payload_json,'$.work_schema')=?",
                (RESEARCH_WORK_SCHEMA_VERSION,),
            )
        return tuple(
            PreparedResearchWork(
                row["work_id"], row["payload_json"],
                datetime.fromisoformat(row["enqueued_at"]),
            )
            for row in rows
        )

    def completeness_snapshot(self) -> Mapping[str, Any]:
        """Return bounded durable reconciliation state without loading rows."""

        with self._connect() as connection:
            work = dict(connection.execute(
                "SELECT state,COUNT(*) FROM research_work_items GROUP BY state"
            ).fetchall())
            candidate = dict(connection.execute(
                """SELECT CASE WHEN json_extract(labels_json,'$.outcome_status')
                            ='COMPLETE' THEN 'COMPLETE' ELSE 'PENDING' END,
                          COUNT(*)
                   FROM experiment_candidates GROUP BY 1"""
            ).fetchall())
            active = connection.execute(
                "SELECT COUNT(*) FROM research_active_candidates"
            ).fetchone()[0]
            oldest = connection.execute(
                """SELECT MIN(enqueued_at) FROM research_work_items
                   WHERE state IN ('CHECKPOINTED','STARTED')"""
            ).fetchone()[0]
            legacy_outstanding = connection.execute(
                """SELECT COUNT(*) FROM research_work_items
                   WHERE state IN ('CHECKPOINTED','STARTED')
                     AND (json_valid(payload_json)=0 OR
                          COALESCE(json_extract(payload_json,'$.work_schema'),'')
                          != ?)""",
                (RESEARCH_WORK_SCHEMA_VERSION,),
            ).fetchone()[0]
            resumed = connection.execute(
                "SELECT COUNT(*) FROM research_work_items WHERE attempts>1"
            ).fetchone()[0]
            telemetry = connection.execute(
                "SELECT * FROM research_worker_telemetry WHERE singleton=1"
            ).fetchone()
        return {
            "items_accepted": sum(work.values()),
            "items_completed": work.get("COMPLETED", 0),
            "items_checkpointed": work.get("CHECKPOINTED", 0),
            "items_started": work.get("STARTED", 0),
            "items_resumed": max(
                resumed, 0 if telemetry is None else telemetry["resumed"]
            ),
            "items_rejected": (
                0 if telemetry is None else telemetry["items_rejected"]
            ),
            "queue_high_water": (
                0 if telemetry is None else telemetry["queue_high_water"]
            ),
            "worker_lag_max_ms": (
                0.0 if telemetry is None else telemetry["lag_max_ms"]
            ),
            "durable_outstanding": (
                work.get("CHECKPOINTED", 0) + work.get("STARTED", 0)
            ),
            "oldest_outstanding_at": oldest,
            "legacy_outstanding_count": legacy_outstanding,
            "active_candidate_count": active,
            "complete_candidate_count": candidate.get("COMPLETE", 0),
            "pending_candidate_count": candidate.get("PENDING", 0),
        }

    def record_worker_telemetry(
        self, *, rejected: int, queue_high_water: int,
        lag_max_ms: float, resumed: int,
    ) -> None:
        """Persist bounded aggregate completeness telemetry off the producer."""

        with self._connect() as connection:
            connection.execute(
                """INSERT INTO research_worker_telemetry(
                   singleton,items_rejected,queue_high_water,lag_max_ms,resumed,updated_at
                   ) VALUES(1,?,?,?,?,?)
                   ON CONFLICT(singleton) DO UPDATE SET
                   items_rejected=MAX(items_rejected,excluded.items_rejected),
                   queue_high_water=MAX(queue_high_water,excluded.queue_high_water),
                   lag_max_ms=MAX(lag_max_ms,excluded.lag_max_ms),
                   resumed=resumed+excluded.resumed,
                   updated_at=excluded.updated_at""",
                (rejected, queue_high_water, lag_max_ms, resumed,
                 datetime.now(UTC).isoformat()),
            )

    def active_query_plan(self, symbol: str, timestamp: datetime) -> tuple[str, ...]:
        """Expose the bounded hot-query plan for diagnostics and tests."""

        if timestamp.tzinfo is None:
            raise ValueError("query-plan timestamp must be timezone-aware")
        with self._connect() as connection:
            rows = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT c.* FROM research_active_candidates AS a
                   JOIN experiment_candidates AS c USING(candidate_id)
                   WHERE a.symbol=? AND a.decision_timestamp<=?
                   ORDER BY a.decision_timestamp,a.candidate_id""",
                (symbol.strip().upper(), timestamp.astimezone(UTC).isoformat()),
            ).fetchall()
        return tuple(str(row[3]) for row in rows)

    def process_prepared_work(
        self, work: PreparedResearchWork
    ) -> CandidateRecord:
        with self._connect() as connection:
            candidate_id = self._process_prepared_work(connection, work)
        return self.get(candidate_id)

    def process_prepared_batch(
        self, items: Iterable[PreparedResearchWork]
    ) -> tuple[str, ...]:
        """Process a bounded FIFO batch in one atomic transaction."""
        batch = tuple(items)
        if not batch:
            return ()
        with self._connect() as connection:
            placeholders = ",".join("?" for _ in batch)
            states = {
                row["work_id"]: row["state"] for row in connection.execute(
                    f"SELECT work_id,state FROM research_work_items "
                    f"WHERE work_id IN ({placeholders})",
                    tuple(item.work_id for item in batch),
                )
            }
            pending = tuple(
                item for item in batch
                if states.get(item.work_id) != "COMPLETED"
            )
            if pending:
                pending_placeholders = ",".join("?" for _ in pending)
                started_at = datetime.now(UTC).isoformat()
                connection.execute(
                    f"""UPDATE research_work_items
                        SET state='STARTED',attempts=attempts+1,
                            started_at=?,error_type=NULL
                        WHERE work_id IN ({pending_placeholders})""",
                    (started_at, *(item.work_id for item in pending)),
                )
            results: list[str] = []
            observations: list[PreparedResearchWork] = []

            def flush_observations() -> None:
                if observations:
                    self._process_observation_batch(connection, observations)
                    observations.clear()

            for item in batch:
                payload = item.payload
                candidate_id = payload.get("candidate_id")
                if states.get(item.work_id) == "COMPLETED":
                    results.append("" if candidate_id is None else str(candidate_id))
                elif candidate_id is None:
                    observations.append(item)
                    results.append("")
                else:
                    flush_observations()
                    results.append(self._process_prepared_work(
                        connection, item, manage_ledger=False
                    ))
            flush_observations()
            if pending:
                completed_at = datetime.now(UTC).isoformat()
                connection.execute(
                    f"""UPDATE research_work_items
                        SET state='COMPLETED',completed_at=?
                        WHERE work_id IN ({pending_placeholders})""",
                    (completed_at, *(item.work_id for item in pending)),
                )
            return tuple(results)

    def _process_observation_batch(
        self, connection: sqlite3.Connection,
        items: Iterable[PreparedResearchWork],
    ) -> None:
        """Apply observation-only work with one active lookup per symbol.

        Every source observation is visited in FIFO order.  Only the database
        lookup/write shape is coalesced; intermediate extrema and the first
        observation at or after every horizon are retained exactly.
        """

        by_symbol: dict[str, list[tuple[datetime, Decimal]]] = {}
        for item in items:
            payload = item.payload
            if payload.get("work_schema") != RESEARCH_WORK_SCHEMA_VERSION:
                raise ValueError("unsupported research work schema")
            observation = payload.get("observation")
            if not isinstance(observation, dict):
                raise ValueError("malformed research work observation")
            symbol = str(observation["symbol"]).strip().upper()
            timestamp = datetime.fromisoformat(str(observation["timestamp"]))
            price = Decimal(str(observation["price"]))
            if not symbol or timestamp.tzinfo is None or not price.is_finite() or price <= 0:
                raise ValueError("invalid research price observation")
            by_symbol.setdefault(symbol, []).append((timestamp.astimezone(UTC), price))

        for symbol, observations in by_symbol.items():
            latest = max(timestamp for timestamp, _price in observations)
            rows = connection.execute(
                """SELECT c.*,a.counterfactual_pending,a.execution_active,
                          a.mfe_so_far AS active_mfe,
                          a.mae_so_far AS active_mae,
                          a.last_observation_time AS active_last_observed,
                          a.extrema_initialized AS active_extrema_initialized
                   FROM research_active_candidates AS a
                   JOIN experiment_candidates AS c USING(candidate_id)
                   WHERE a.symbol=? AND a.decision_timestamp<=?
                   ORDER BY a.decision_timestamp,a.candidate_id""",
                (symbol, latest.isoformat()),
            ).fetchall()
            for row in rows:
                decision_at = datetime.fromisoformat(row["decision_timestamp"])
                features = json.loads(row["features_json"])
                labels = json.loads(row["labels_json"])
                execution = json.loads(row["execution_json"])
                counterfactual_active = bool(row["counterfactual_pending"])
                execution_active = bool(row["execution_active"])
                mfe = Decimal(str(row["active_mfe"]))
                mae = Decimal(str(row["active_mae"]))
                extrema_initialized = bool(row["active_extrema_initialized"])
                touched = False
                for observed_at, observed in observations:
                    elapsed = (observed_at - decision_at).total_seconds()
                    if elapsed < 0:
                        continue
                    touched = True
                    if counterfactual_active:
                        reference = Decimal(features["counterfactual_reference_price"])
                        move = (observed - reference) / reference
                        if elapsed <= HORIZONS_SECONDS["30m"]:
                            mfe = max(mfe, move)
                            mae = min(mae, move)
                            extrema_initialized = True
                        labels["last_observed_at"] = observed_at.isoformat()
                        for name, seconds in HORIZONS_SECONDS.items():
                            price_key = f"price_after_{name}"
                            if elapsed >= seconds and price_key not in labels:
                                labels[price_key] = str(observed)
                                labels[f"return_after_{name}"] = str(move)
                        labels["outcome_status"] = (
                            "COMPLETE" if "price_after_30m" in labels else "PENDING"
                        )
                        labels["mfe"] = str(mfe)
                        labels["mae"] = str(mae)
                        if labels["outcome_status"] == "COMPLETE":
                            counterfactual_active = False
                    entry_at_text = execution.get("entry_timestamp")
                    average_fill_text = execution.get("average_fill_price")
                    actual_active = False
                    if entry_at_text is not None and average_fill_text is not None:
                        entry_at = datetime.fromisoformat(entry_at_text)
                        exit_at_text = execution.get("exit_timestamp")
                        exit_at = (
                            None if exit_at_text is None else
                            datetime.fromisoformat(exit_at_text)
                        )
                        actual_active = (
                            observed_at >= entry_at
                            and (exit_at is None or observed_at <= exit_at)
                        )
                        if actual_active:
                            actual_reference = Decimal(average_fill_text)
                            side = execution.get("side", "BUY")
                            actual_move = (
                                (observed - actual_reference) / actual_reference
                                if side == "BUY" else
                                (actual_reference - observed) / actual_reference
                            )
                            execution["actual_mfe"] = str(max(
                                Decimal(execution.get("actual_mfe", "0")),
                                actual_move,
                            ))
                            execution["actual_mae"] = str(min(
                                Decimal(execution.get("actual_mae", "0")),
                                actual_move,
                            ))
                    if not counterfactual_active and not execution_active:
                        break
                if not touched:
                    continue
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """UPDATE experiment_candidates
                       SET labels_json=?,execution_json=?,updated_at=?
                       WHERE candidate_id=?""",
                    (_json(labels), _json(execution), now, row["candidate_id"]),
                )
                self._sync_active_row(connection, row, labels, execution)
                self._last_price_observation[symbol] = observations[-1]

    def _process_prepared_work(
        self, connection: sqlite3.Connection, work: PreparedResearchWork,
        *, manage_ledger: bool = True,
    ) -> str:
        payload = work.payload
        if payload.get("work_schema") != RESEARCH_WORK_SCHEMA_VERSION:
            raise ValueError("legacy research work requires explicit recovery treatment")
        observation = payload.get("observation")
        if not isinstance(observation, dict):
            raise ValueError("malformed research work observation")
        price_timestamp = datetime.fromisoformat(str(observation["timestamp"]))
        price = Decimal(str(observation["price"]))
        symbol = str(observation["symbol"]).strip().upper()
        features = payload.get("candidate_features")
        candidate_id_value = payload.get("candidate_id")
        if (features is None) != (candidate_id_value is None):
            raise ValueError("candidate identity and features must be paired")
        if features is not None and not isinstance(features, dict):
            raise ValueError("malformed research work candidate features")
        candidate_id = "" if candidate_id_value is None else str(candidate_id_value)
        execution = {
            "state": ExecutionState.NOT_EXECUTED.value,
            "paper_trade_executed": False,
            "fill_ids": [],
            "fill_quantity": "0",
        }
        ledger = None
        if manage_ledger:
            ledger = connection.execute(
                "SELECT state FROM research_work_items WHERE work_id=?",
                (work.work_id,),
            ).fetchone()
            if ledger is not None and ledger["state"] == "COMPLETED":
                return candidate_id
            if ledger is not None:
                connection.execute(
                    """UPDATE research_work_items
                       SET state='STARTED',attempts=attempts+1,
                           started_at=?,error_type=NULL WHERE work_id=?""",
                    (datetime.now(UTC).isoformat(), work.work_id),
                )
        observation = (price_timestamp, price)
        if self._last_price_observation.get(symbol) != observation:
            self._observe_price(connection, symbol, price_timestamp, price)
            self._last_price_observation[symbol] = observation
        if features is not None:
            if str(features.get("symbol", "")).strip().upper() != symbol:
                raise ValueError("candidate and observation symbols differ")
            encoded_features = _json(features)
            existing = connection.execute(
                "SELECT * FROM experiment_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if existing is None:
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """INSERT INTO experiment_candidates(
                       candidate_id,trade_id,symbol,decision_timestamp,features_json,
                       labels_json,execution_json,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (candidate_id, None, symbol, features["decision_timestamp"],
                     encoded_features, _json({}), _json(execution), now, now),
                )
                existing = connection.execute(
                    "SELECT * FROM experiment_candidates WHERE candidate_id=?",
                    (candidate_id,),
                ).fetchone()
                assert existing is not None
                self._sync_active_row(connection, existing, {}, execution)
            elif existing["features_json"] != encoded_features:
                raise ValueError("immutable candidate decision snapshot mismatch")
        if manage_ledger and ledger is not None:
            connection.execute(
                """UPDATE research_work_items SET state='COMPLETED',
                   completed_at=? WHERE work_id=?""",
                (datetime.now(UTC).isoformat(), work.work_id),
            )
        return candidate_id

    def observe_price(
        self, symbol: str, timestamp: datetime, price: Decimal
    ) -> int:
        normalized = symbol.strip().upper()
        if not normalized or timestamp.tzinfo is None:
            raise ValueError("symbol and timezone-aware timestamp are required")
        observed = Decimal(price)
        if not observed.is_finite() or observed <= 0:
            raise ValueError("observed price must be positive and finite")
        with self._connect() as connection:
            return self._observe_price(connection, normalized, timestamp, observed)

    def _observe_price(
        self, connection: sqlite3.Connection, symbol: str,
        timestamp: datetime, observed: Decimal,
    ) -> int:
        changed = 0
        observed_at = timestamp.astimezone(UTC)
        rows = connection.execute(
            """SELECT c.*,a.counterfactual_pending,a.execution_active,
                      a.mfe_so_far AS active_mfe,
                      a.mae_so_far AS active_mae,
                      a.last_observation_time AS active_last_observed,
                      a.extrema_initialized AS active_extrema_initialized
               FROM research_active_candidates AS a
               JOIN experiment_candidates AS c USING(candidate_id)
               WHERE a.symbol=? AND a.decision_timestamp<=?
                 AND (a.next_due_at<=? OR a.execution_active=1)
               ORDER BY a.decision_timestamp,a.candidate_id""",
            (symbol, observed_at.isoformat(), observed_at.isoformat()),
        ).fetchall()
        touched = connection.execute(
            """UPDATE research_active_candidates
               SET last_observation_time=?
               WHERE symbol=? AND decision_timestamp<=?
                 AND counterfactual_pending=1""",
            (observed_at.isoformat(), symbol, observed_at.isoformat()),
        )
        changed = max(0, touched.rowcount)
        mfe_cutoff = observed_at - timedelta(seconds=HORIZONS_SECONDS["30m"])
        connection.execute(
            """UPDATE research_active_candidates
               SET mfe_so_far=research_mfe(mfe_so_far,?,reference_price),
                   mae_so_far=research_mae(mae_so_far,?,reference_price),
                   extrema_initialized=1
               WHERE symbol=? AND decision_timestamp BETWEEN ? AND ?
                 AND counterfactual_pending=1""",
            (str(observed), str(observed), symbol, mfe_cutoff.isoformat(),
             observed_at.isoformat()),
        )
        for row in rows:
                decision_at = datetime.fromisoformat(row["decision_timestamp"])
                elapsed = (observed_at - decision_at).total_seconds()
                if elapsed < 0:
                    continue
                features = json.loads(row["features_json"])
                labels = json.loads(row["labels_json"])
                execution = json.loads(row["execution_json"])
                counterfactual_active = bool(row["counterfactual_pending"])
                if counterfactual_active:
                    reference = Decimal(features["counterfactual_reference_price"])
                    move = (observed - reference) / reference
                    if elapsed <= HORIZONS_SECONDS["30m"]:
                        labels["mfe"] = str(max(
                            Decimal(labels.get("mfe", "0")), move
                        ))
                        labels["mae"] = str(min(
                            Decimal(labels.get("mae", "0")), move
                        ))
                    labels["last_observed_at"] = observed_at.isoformat()
                    for name, seconds in HORIZONS_SECONDS.items():
                        price_key = f"price_after_{name}"
                        if elapsed >= seconds and price_key not in labels:
                            labels[price_key] = str(observed)
                            labels[f"return_after_{name}"] = str(move)
                    labels["outcome_status"] = (
                        "COMPLETE" if "price_after_30m" in labels else "PENDING"
                    )
                    if elapsed <= HORIZONS_SECONDS["30m"]:
                        labels["mfe"] = _research_mfe(
                            str(row["active_mfe"]), str(observed),
                            str(features["counterfactual_reference_price"]),
                        )
                        labels["mae"] = _research_mae(
                            str(row["active_mae"]), str(observed),
                            str(features["counterfactual_reference_price"]),
                        )
                    elif bool(row["active_extrema_initialized"]):
                        labels["mfe"] = str(row["active_mfe"])
                        labels["mae"] = str(row["active_mae"])
                entry_at_text = execution.get("entry_timestamp")
                average_fill_text = execution.get("average_fill_price")
                actual_active = False
                if entry_at_text is not None and average_fill_text is not None:
                    entry_at = datetime.fromisoformat(entry_at_text)
                    exit_at_text = execution.get("exit_timestamp")
                    exit_at = (
                        None if exit_at_text is None
                        else datetime.fromisoformat(exit_at_text)
                    )
                    actual_active = (
                        timestamp.astimezone(UTC) >= entry_at
                        and (exit_at is None or timestamp.astimezone(UTC) <= exit_at)
                    )
                    if actual_active:
                        actual_reference = Decimal(average_fill_text)
                        side = execution.get("side", "BUY")
                        actual_move = (
                            (observed - actual_reference) / actual_reference
                            if side == "BUY"
                            else (actual_reference - observed) / actual_reference
                        )
                        execution["actual_mfe"] = str(max(
                            Decimal(execution.get("actual_mfe", "0")), actual_move
                        ))
                        execution["actual_mae"] = str(min(
                            Decimal(execution.get("actual_mae", "0")), actual_move
                        ))
                if not counterfactual_active and not actual_active:
                    continue
                connection.execute(
                    "UPDATE experiment_candidates SET labels_json=?,execution_json=?,updated_at=? WHERE candidate_id=?",
                    (
                        _json(labels), _json(execution), datetime.now(UTC).isoformat(),
                        row["candidate_id"],
                    ),
                )
                if not counterfactual_active and actual_active:
                    changed += 1
                self._sync_active_row(connection, row, labels, execution)
        return changed

    def _sync_active_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row,
        labels: Mapping[str, Any], execution: Mapping[str, Any],
    ) -> None:
        pending = labels.get("outcome_status") != "COMPLETE"
        execution_active = execution.get("state") in {
            ExecutionState.PARTIALLY_FILLED.value,
            ExecutionState.FILLED.value,
        }
        if not pending and not execution_active:
            connection.execute(
                "DELETE FROM research_active_candidates WHERE candidate_id=?",
                (row["candidate_id"],),
            )
            return
        completed = [
            name for name in HORIZONS_SECONDS
            if f"price_after_{name}" in labels
        ]
        next_horizon = next(
            (name for name in HORIZONS_SECONDS if name not in completed), None
        )
        next_due = (
            None if next_horizon is None else
            datetime.fromisoformat(row["decision_timestamp"])
            + timedelta(seconds=HORIZONS_SECONDS[next_horizon])
        )
        features = json.loads(row["features_json"])
        connection.execute(
            """INSERT INTO research_active_candidates(
               candidate_id,symbol,decision_timestamp,reference_price,
               next_due_horizon,next_due_at,completed_horizons_json,
               mfe_so_far,mae_so_far,last_observation_time,
               counterfactual_pending,execution_active,extrema_initialized
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(candidate_id) DO UPDATE SET
               next_due_horizon=excluded.next_due_horizon,
               next_due_at=excluded.next_due_at,
               completed_horizons_json=excluded.completed_horizons_json,
               mfe_so_far=excluded.mfe_so_far,
               mae_so_far=excluded.mae_so_far,
               last_observation_time=excluded.last_observation_time,
               counterfactual_pending=excluded.counterfactual_pending,
               execution_active=excluded.execution_active,
               extrema_initialized=excluded.extrema_initialized""",
            (row["candidate_id"], row["symbol"], row["decision_timestamp"],
             str(features["counterfactual_reference_price"]), next_horizon,
             None if next_due is None else next_due.isoformat(),
             _json({"completed": completed}),
             str(labels.get("mfe", row["active_mfe"] if "active_mfe" in row.keys() else "0")),
             str(labels.get("mae", row["active_mae"] if "active_mae" in row.keys() else "0")),
             labels.get("last_observed_at"),
             int(pending), int(execution_active),
             int("mfe" in labels or "mae" in labels)),
        )

    def record_submission(
        self,
        candidate_id: str,
        *,
        requested_quantity: Decimal,
        order_type: str,
        submitted_price: Decimal | None,
        client_order_id: str | None = None,
        order_id: str | None = None,
        accepted: bool = True,
        rejection_reason: str | None = None,
        live_trading_enabled: bool = False,
        side: str = "BUY",
        stop_price: Decimal | None = None,
        target_price: Decimal | None = None,
        risk_per_share: Decimal | None = None,
        planned_risk_dollars: Decimal | None = None,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        _require_paper_execution(record, live_trading_enabled)
        if not bool(record.features["normal_qualifies"]):
            raise ValueError("normal strategy did not qualify; execution denied")
        quantity = Decimal(requested_quantity)
        if quantity <= 0:
            raise ValueError("requested quantity must be positive")
        deterministic_client_id = client_order_id or f"atlas-{candidate_id[:24]}"
        deterministic_trade_id = f"trade-{candidate_id}"
        execution = dict(record.execution)
        if execution.get("state") != ExecutionState.NOT_EXECUTED.value:
            if execution.get("client_order_id") == deterministic_client_id:
                return record
            raise ValueError("candidate already has an execution attempt")
        execution.update({
            "state": (
                ExecutionState.ACCEPTED.value if accepted
                else ExecutionState.REJECTED.value
            ),
            "paper_trade_executed": False,
            "requested_quantity": str(quantity),
            "order_type": order_type.strip().upper(),
            "side": side.strip().upper(),
            "submitted_price": None if submitted_price is None else str(submitted_price),
            "client_order_id": deterministic_client_id,
            "order_id": order_id,
            "rejection_reason": rejection_reason,
            "submitted_at": datetime.now(UTC).isoformat(),
            "state_history": [
                ExecutionState.SUBMITTED.value,
                (
                    ExecutionState.ACCEPTED.value if accepted
                    else ExecutionState.REJECTED.value
                ),
            ],
            "stop_price": _decimal_text(stop_price),
            "target_price": _decimal_text(target_price),
            "risk_per_share": _decimal_text(risk_per_share),
            "planned_risk_dollars": _decimal_text(planned_risk_dollars),
        })
        return self._update_execution(candidate_id, deterministic_trade_id, execution)

    def record_fill(
        self,
        candidate_id: str,
        *,
        fill_id: str,
        quantity: Decimal,
        price: Decimal,
        timestamp: datetime,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        _require_paper_execution(record, False)
        execution = dict(record.execution)
        if execution.get("state") not in {
            ExecutionState.ACCEPTED.value,
            ExecutionState.PARTIALLY_FILLED.value,
        }:
            raise ValueError("fill requires an accepted paper order")
        if fill_id in execution.get("fill_ids", []):
            return record
        fill_quantity = Decimal(quantity)
        fill_price = Decimal(price)
        if fill_quantity <= 0 or fill_price <= 0 or timestamp.tzinfo is None:
            raise ValueError("fill quantity, price, and aware timestamp are required")
        previous_quantity = Decimal(execution.get("fill_quantity", "0"))
        previous_average = Decimal(execution.get("average_fill_price") or "0")
        total_quantity = previous_quantity + fill_quantity
        requested = Decimal(execution["requested_quantity"])
        if total_quantity > requested:
            raise ValueError("fill quantity exceeds requested quantity")
        average = (
            previous_average * previous_quantity + fill_price * fill_quantity
        ) / total_quantity
        fill_ids = list(execution.get("fill_ids", [])) + [fill_id]
        reference = Decimal(record.features["last_price"])
        execution.update({
            "state": (
                ExecutionState.FILLED.value
                if total_quantity == requested
                else ExecutionState.PARTIALLY_FILLED.value
            ),
            "paper_trade_executed": True,
            "fill_ids": fill_ids,
            "fill_quantity": str(total_quantity),
            "average_fill_price": str(average),
            "slippage": str(average - reference),
            "entry_timestamp": execution.get("entry_timestamp")
            or timestamp.astimezone(UTC).isoformat(),
            "state_history": [
                *execution.get("state_history", []),
                (
                    ExecutionState.FILLED.value
                    if total_quantity == requested
                    else ExecutionState.PARTIALLY_FILLED.value
                ),
            ],
        })
        return self._update_execution(candidate_id, record.trade_id, execution)

    def record_cancellation(
        self, candidate_id: str, *, reason: str, timestamp: datetime
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        execution = dict(record.execution)
        if execution.get("state") not in {
            ExecutionState.ACCEPTED.value,
            ExecutionState.PARTIALLY_FILLED.value,
        }:
            raise ValueError("only an open paper order can be cancelled")
        if timestamp.tzinfo is None:
            raise ValueError("cancellation timestamp must be timezone-aware")
        execution.update({
            "state": ExecutionState.CANCELLED.value,
            "cancelled_at": timestamp.astimezone(UTC).isoformat(),
            "cancellation_reason": reason.strip(),
            "state_history": [
                *execution.get("state_history", []), ExecutionState.CANCELLED.value
            ],
        })
        return self._update_execution(candidate_id, record.trade_id, execution)

    def record_replacement(
        self,
        candidate_id: str,
        *,
        replacement_order_id: str,
        replacement_price: Decimal | None,
        timestamp: datetime,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        execution = dict(record.execution)
        if execution.get("state") not in {
            ExecutionState.ACCEPTED.value,
            ExecutionState.PARTIALLY_FILLED.value,
        }:
            raise ValueError("only an open paper order can be replaced")
        if timestamp.tzinfo is None or not replacement_order_id.strip():
            raise ValueError("replacement identity and aware timestamp are required")
        replacements = list(execution.get("replacements", []))
        replacements.append({
            "order_id": replacement_order_id.strip(),
            "price": None if replacement_price is None else str(replacement_price),
            "timestamp": timestamp.astimezone(UTC).isoformat(),
        })
        execution["replacements"] = replacements
        return self._update_execution(candidate_id, record.trade_id, execution)

    def record_exit(
        self,
        candidate_id: str,
        *,
        exit_price: Decimal,
        exit_reason: str,
        timestamp: datetime,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        execution = dict(record.execution)
        quantity = Decimal(execution.get("fill_quantity", "0"))
        entry = Decimal(execution.get("average_fill_price") or "0")
        price = Decimal(exit_price)
        if quantity <= 0 or entry <= 0:
            raise ValueError("actual filled quantity is required before exit")
        if price <= 0 or timestamp.tzinfo is None:
            raise ValueError("positive exit price and aware timestamp are required")
        entry_at = datetime.fromisoformat(execution["entry_timestamp"])
        exit_move = (
            (price - entry) / entry
            if execution.get("side", "BUY") == "BUY"
            else (entry - price) / entry
        )
        realized_pnl = (
            (price - entry) * quantity
            if execution.get("side", "BUY") == "BUY"
            else (entry - price) * quantity
        )
        execution.update({
            "state": ExecutionState.CLOSED.value,
            "exit_timestamp": timestamp.astimezone(UTC).isoformat(),
            "exit_price": str(price),
            "exit_reason": exit_reason.strip(),
            "realized_pnl": str(realized_pnl),
            "return_percent": str(exit_move * Decimal("100")),
            "actual_mfe": str(max(
                Decimal(execution.get("actual_mfe", "0")), exit_move
            )),
            "actual_mae": str(min(
                Decimal(execution.get("actual_mae", "0")), exit_move
            )),
            "holding_seconds": int((timestamp.astimezone(UTC) - entry_at).total_seconds()),
            "state_history": [
                *execution.get("state_history", []), ExecutionState.CLOSED.value
            ],
        })
        return self._update_execution(candidate_id, record.trade_id, execution)

    def get(self, candidate_id: str) -> CandidateRecord:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT c.*,a.mfe_so_far AS active_mfe,
                          a.mae_so_far AS active_mae,
                          a.last_observation_time AS active_last_observed,
                          a.extrema_initialized AS active_extrema_initialized
                   FROM experiment_candidates AS c
                   LEFT JOIN research_active_candidates AS a USING(candidate_id)
                   WHERE c.candidate_id=?""",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return _record(row)

    def records(self) -> tuple[CandidateRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT c.*,a.mfe_so_far AS active_mfe,
                          a.mae_so_far AS active_mae,
                          a.last_observation_time AS active_last_observed,
                          a.extrema_initialized AS active_extrema_initialized
                   FROM experiment_candidates AS c
                   LEFT JOIN research_active_candidates AS a USING(candidate_id)
                   ORDER BY c.decision_timestamp,c.candidate_id"""
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def pending(self, symbol: str | None = None) -> tuple[CandidateRecord, ...]:
        records = self.records()
        return tuple(
            item for item in records
            if (symbol is None or item.symbol == symbol.strip().upper())
            and item.labels.get("outcome_status") != "COMPLETE"
        )

    def record_coordination_result(self, coordination: object) -> CandidateRecord | None:
        """Project an existing coordinator result; never performs execution."""

        decision = getattr(coordination, "strategy_decision", None)
        intent = getattr(coordination, "order_intent", None)
        if decision is None or intent is None:
            return None
        action = str(getattr(getattr(decision, "action", None), "value", ""))
        symbol = str(getattr(decision, "symbol", "")).strip().upper()
        if action.startswith("EXIT_"):
            opened = tuple(
                item for item in self.records()
                if item.symbol == symbol
                and item.execution.get("paper_trade_executed") is True
                and item.execution.get("state") in {
                    ExecutionState.FILLED.value,
                    ExecutionState.PARTIALLY_FILLED.value,
                }
            )
            simulation = getattr(coordination, "execution_result", None)
            execution_result = getattr(simulation, "execution", None)
            fill = getattr(execution_result, "fill", None)
            if not opened or fill is None:
                return None
            return self.record_exit(
                opened[-1].candidate_id, exit_price=fill.fill_price,
                exit_reason="EXISTING_STRATEGY_EXIT", timestamp=fill.timestamp,
            )

        if not action.startswith("ENTER_"):
            return None
        eligible = tuple(
            item for item in self.records()
            if item.symbol == symbol
            and item.features.get("normal_qualifies") is True
            and item.execution.get("state") == ExecutionState.NOT_EXECUTED.value
            and datetime.fromisoformat(str(item.features["decision_timestamp"]))
            <= decision.timestamp.astimezone(UTC)
        )
        if not eligible:
            return None
        candidate = eligible[-1]
        status = str(getattr(getattr(coordination, "status", None), "value", ""))
        simulation = getattr(coordination, "execution_result", None)
        execution_result = getattr(simulation, "execution", None)
        fill = getattr(execution_result, "fill", None)
        accepted = status == "EXECUTED" and fill is not None
        candidate = self.record_submission(
            candidate.candidate_id,
            requested_quantity=intent.quantity,
            order_type=intent.order_type.value,
            submitted_price=intent.limit_price,
            client_order_id=intent.request_id,
            order_id=intent.request_id,
            accepted=accepted,
            rejection_reason=None if accepted else _coordination_reason(coordination),
            live_trading_enabled=False,
            side=intent.side.value,
            stop_price=intent.stop_price,
        )
        execution = dict(candidate.execution)
        execution["execution_strategy_version"] = str(
            getattr(decision, "strategy_version", "unknown")
        )
        candidate = self._update_execution(
            candidate.candidate_id, candidate.trade_id, execution
        )
        if intent.stop_price is not None:
            execution = dict(candidate.execution)
            reference = Decimal(candidate.features["last_price"])
            risk_per_share = abs(reference - intent.stop_price)
            execution.update({
                "stop_price": str(intent.stop_price),
                "risk_per_share": str(risk_per_share),
                "planned_risk_dollars": str(risk_per_share * intent.quantity),
            })
            candidate = self._update_execution(
                candidate.candidate_id, candidate.trade_id, execution
            )
        if fill is None:
            return candidate
        return self.record_fill(
            candidate.candidate_id,
            fill_id=f"{fill.request_id}:{fill.timestamp.astimezone(UTC).isoformat()}",
            quantity=fill.quantity,
            price=fill.fill_price,
            timestamp=fill.timestamp,
        )

    def _update_execution(
        self, candidate_id: str, trade_id: str | None, execution: Mapping[str, Any]
    ) -> CandidateRecord:
        _assert_no_secrets(execution)
        with self._connect() as connection:
            connection.execute(
                "UPDATE experiment_candidates SET trade_id=?,execution_json=?,updated_at=? WHERE candidate_id=?",
                (trade_id, _json(execution), datetime.now(UTC).isoformat(), candidate_id),
            )
            row = connection.execute(
                "SELECT * FROM experiment_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            assert row is not None
            self._sync_active_row(
                connection, row, json.loads(row["labels_json"]), execution
            )
        return self.get(candidate_id)


def _decision_features(
    decision: ScannerDecision,
    *, market_session: str, scanner_rank: int | None,
    strategy_version: str, model_version: str,
    application_commit: str | None, execution_environment: str,
) -> dict[str, Any]:
    source_url = _safe_url(decision.catalyst_source_url)
    return {
        "feature_schema": SCHEMA_VERSION,
        "strategy_version": strategy_version,
        "model_version": model_version,
        "application_commit": application_commit,
        "execution_environment": execution_environment,
        "symbol": decision.symbol,
        "market_session": market_session.strip().upper(),
        "decision_timestamp": decision.timestamp.astimezone(UTC).isoformat(),
        "scanner_rank": decision.scanner_rank if scanner_rank is None else scanner_rank,
        "scanner_score": decision.score,
        "last_price": str(decision.price),
        "last_price_timestamp": (
            None if decision.last_price_timestamp is None
            else decision.last_price_timestamp.isoformat()
        ),
        "quote_timestamp": (
            None if decision.quote_timestamp is None
            else decision.quote_timestamp.isoformat()
        ),
        "evaluation_timestamp": (
            None if decision.observed_at is None
            else decision.observed_at.isoformat()
        ),
        "last_price_received_timestamp": (
            None if decision.last_price_received_timestamp is None
            else decision.last_price_received_timestamp.isoformat()
        ),
        "quote_received_timestamp": (
            None if decision.quote_received_timestamp is None
            else decision.quote_received_timestamp.isoformat()
        ),
        "previous_close": _decimal_text(decision.previous_close),
        "percentage_change": str(decision.metrics.percentage_change),
        "relative_volume": str(decision.metrics.relative_volume),
        "current_volume": _decimal_text(decision.current_volume),
        "average_30_day_volume": _decimal_text(decision.average_30_day_volume),
        "float_shares": _decimal_text(decision.float_shares),
        "dollar_volume": str(decision.metrics.dollar_volume),
        "bid": _decimal_text(decision.bid),
        "ask": _decimal_text(decision.ask),
        "spread_percent": _decimal_text(decision.metrics.spread_percent),
        "tradable": decision.tradable,
        "halted": decision.halted,
        "catalyst_status": decision.catalyst_status.value,
        "catalyst_type": decision.catalyst.value,
        "selected_source": decision.catalyst_source,
        "headline": decision.catalyst_headline,
        "published_at": (
            decision.catalyst_published_at.astimezone(UTC).isoformat()
            if decision.catalyst_published_at is not None else None
        ),
        "source_url": source_url,
        "corroborating_sources": list(decision.corroborating_sources),
        "evidence_count": decision.catalyst_evidence_count,
        "event_count": decision.catalyst_event_count,
        "normal_qualifies": decision.qualified,
        "technical_qualifies_without_catalyst": decision.technical_qualifies_without_catalyst,
        "passed_rules": list(decision.passed_rules),
        "failed_rules": list(decision.failed_rules),
        "technical_passed_rules": list(decision.technical_passed_rules),
        "technical_failed_rules": list(decision.technical_failed_rules),
        "cohort_flags": list(decision.cohort_flags),
        "counterfactual_reference_price": str(decision.price),
        "counterfactual_reference_kind": "OBSERVED_MARKET_PRICE_AT_DECISION",
    }


def _candidate_id(features: Mapping[str, Any]) -> str:
    payload = _json(features)
    return "candidate-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def logical_decision_state(decision: ScannerDecision) -> Mapping[str, Any]:
    """Return the categorical scanner-opportunity state for episode detection.

    Continuous tick measurements, score, and rank are intentionally absent.
    They are captured in the immutable features at episode entry, but changes
    to them do not by themselves constitute a new scanner decision.
    """

    if decision.timestamp is None:
        raise ValueError("complete scanner decision timestamp is required")
    from app.live_scanner.session import scanner_session

    return {
        "identity_schema": "atlas-logical-scanner-candidate-v1",
        "symbol": decision.symbol.strip().upper(),
        "market_session": scanner_session(decision.timestamp).value,
        "policy_version": decision.policy_version,
        "normal_qualifies": decision.qualified,
        "technical_qualifies_without_catalyst": (
            decision.technical_qualifies_without_catalyst
        ),
        "passed_rules": list(decision.passed_rules),
        "failed_rules": list(decision.failed_rules),
        "technical_passed_rules": list(decision.technical_passed_rules),
        "technical_failed_rules": list(decision.technical_failed_rules),
        "cohort_flags": list(decision.cohort_flags),
        "tradable": decision.tradable,
        "halted": decision.halted,
        "catalyst_status": decision.catalyst_status.value,
        "catalyst_type": decision.catalyst.value,
        "catalyst_event": {
            "headline": decision.catalyst_headline,
            "published_at": (
                None if decision.catalyst_published_at is None else
                decision.catalyst_published_at.astimezone(UTC).isoformat()
            ),
            "source": decision.catalyst_source,
            "source_url": _safe_url(decision.catalyst_source_url),
            "corroborating_sources": list(decision.corroborating_sources),
            "evidence_count": decision.catalyst_evidence_count,
            "event_count": decision.catalyst_event_count,
        },
    }


def logical_decision_state_signature(decision: ScannerDecision) -> str:
    return hashlib.sha256(
        _json(logical_decision_state(decision)).encode("utf-8")
    ).hexdigest()


def logical_candidate_identity(
    decision: ScannerDecision,
    *,
    state_signature: str,
    execution_environment: str,
    strategy_version: str = DEFAULT_STRATEGY_VERSION,
    model_version: str = DEFAULT_MODEL_VERSION,
) -> Mapping[str, Any]:
    """Identify one transition into an authoritative scanner state."""

    if decision.timestamp is None:
        raise ValueError("complete scanner decision timestamp is required")
    event_identity = decision.source_event_identity or (
        "decision-time:" + decision.timestamp.astimezone(UTC).isoformat()
    )
    return {
        "identity_schema": "atlas-logical-scanner-candidate-v1",
        "symbol": decision.symbol.strip().upper(),
        "episode_event_identity": event_identity,
        "episode_started_at": decision.timestamp.astimezone(UTC).isoformat(),
        "state_signature": state_signature,
        "strategy_version": strategy_version,
        "model_version": model_version,
        "execution_environment": _safe_environment(execution_environment),
    }


def _logical_candidate_id(identity: Mapping[str, Any]) -> str:
    return "candidate-" + hashlib.sha256(
        _json(identity).encode("utf-8")
    ).hexdigest()


def _safe_environment(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in _SAFE_EXECUTION_ENVIRONMENTS:
        raise ValueError("paper experiment execution environment must be PAPER or TEST")
    return normalized


def _safe_application_commit() -> str | None:
    value = os.environ.get("ATLAS_APPLICATION_COMMIT", "").strip()
    return value if re.fullmatch(r"[0-9a-fA-F]{7,64}", value) else None


def _require_paper_execution(record: CandidateRecord, live_enabled: bool) -> None:
    if live_enabled:
        raise PermissionError("LIVE_TRADING_ENABLED must remain false")
    _safe_environment(str(record.features.get("execution_environment", "")))


def _safe_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _assert_no_secrets(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise ValueError(f"secret-bearing journal field rejected: {path}.{key}")
            _assert_no_secrets(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_secrets(item, f"{path}[{index}]")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record(row: sqlite3.Row) -> CandidateRecord:
    labels = json.loads(row["labels_json"])
    keys = row.keys()
    if (
        "active_mfe" in keys
        and row["active_mfe"] is not None
        and row["active_last_observed"] is not None
    ):
        if bool(row["active_extrema_initialized"]):
            labels["mfe"] = str(row["active_mfe"])
            labels["mae"] = str(row["active_mae"])
        labels["last_observed_at"] = str(row["active_last_observed"])
        labels["outcome_status"] = "PENDING"
    return CandidateRecord(
        candidate_id=row["candidate_id"], trade_id=row["trade_id"],
        features=json.loads(row["features_json"]),
        labels=labels,
        execution=json.loads(row["execution_json"]),
    )


def _research_mfe(current: str, observed: str, reference: str) -> str:
    move = (Decimal(observed) - Decimal(reference)) / Decimal(reference)
    return str(max(Decimal(current), move))


def _research_mae(current: str, observed: str, reference: str) -> str:
    move = (Decimal(observed) - Decimal(reference)) / Decimal(reference)
    return str(min(Decimal(current), move))


def read_records(path: str | Path) -> tuple[CandidateRecord, ...]:
    """Read a journal without creating, migrating, or writing it."""

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"paper experiment journal not found: {resolved}")
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        version = connection.execute(
            "SELECT value FROM experiment_metadata WHERE key='schema_version'"
        ).fetchone()
        if version is None or version["value"] != SCHEMA_VERSION:
            raise ValueError("unsupported paper experiment journal schema")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("paper experiment journal integrity check failed")
        active_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='research_active_candidates'"
        ).fetchone()
        if active_table is None:
            rows = connection.execute(
                "SELECT * FROM experiment_candidates "
                "ORDER BY decision_timestamp,candidate_id"
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT c.*,a.mfe_so_far AS active_mfe,
                          a.mae_so_far AS active_mae,
                          a.last_observation_time AS active_last_observed,
                          a.extrema_initialized AS active_extrema_initialized
                   FROM experiment_candidates AS c
                   LEFT JOIN research_active_candidates AS a USING(candidate_id)
                   ORDER BY c.decision_timestamp,c.candidate_id"""
            ).fetchall()
        return tuple(_record(row) for row in rows)
    finally:
        connection.close()


def _coordination_reason(coordination: object) -> str:
    trace = getattr(coordination, "trace", ())
    if trace:
        return str(getattr(trace[-1], "message", "paper execution rejected"))
    return "paper execution rejected"


__all__ = [
    "DEFAULT_MODEL_VERSION", "DEFAULT_STRATEGY_VERSION",
    "INCREMENTAL_ENGINE_VERSION", "PaperTradeExperimentJournal",
    "PreparedResearchWork", "RESEARCH_WORK_SCHEMA_VERSION", "SCHEMA_VERSION",
    "logical_candidate_identity", "logical_decision_state",
    "logical_decision_state_signature", "prepare_price_observation",
    "prepare_research_work", "read_records",
]

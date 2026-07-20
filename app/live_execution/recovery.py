from __future__ import annotations

import json
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from threading import RLock

from app.broker_protocol.models import BrokerOrderRequest, BrokerOrderType, BrokerSide, TimeInForce

SCHEMA_VERSION = 1


class MutationState(StrEnum):
    PREPARED = "PREPARED"
    AUTHORIZED = "AUTHORIZED"
    DISPATCHING = "DISPATCHING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class MutationRecord:
    mutation_id: str
    operation: str
    client_order_id: str
    authorization_id: str
    request: BrokerOrderRequest | None
    state: MutationState
    created_at: datetime
    updated_at: datetime
    broker_order_id: str | None = None


class DurableExecutionJournal:
    """Transactional mutation journal used as the execution idempotency boundary."""

    __slots__ = ("database_path", "_connection", "_lock")

    def __init__(self, database_path: str | Path | None = None):
        if database_path is None:
            handle = tempfile.NamedTemporaryFile(prefix="webull-execution-", suffix=".sqlite3", delete=False)
            handle.close(); database_path = handle.name
        self.database_path = str(Path(database_path).resolve())
        self._lock = RLock()
        self._connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None,
                                           check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS execution_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mutations(
              mutation_id TEXT PRIMARY KEY, operation TEXT NOT NULL, client_order_id TEXT NOT NULL,
              authorization_id TEXT NOT NULL, request_json TEXT, state TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, broker_order_id TEXT,
              schema_version INTEGER NOT NULL,
              UNIQUE(operation,client_order_id)
            );
        """)
        row = self._connection.execute("SELECT value FROM execution_metadata WHERE key='schema_version'").fetchone()
        if row is None:
            self._connection.execute("INSERT INTO execution_metadata VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        elif int(row[0]) != SCHEMA_VERSION: raise ValueError("unsupported execution journal schema")

    def prepare(self, mutation_id, operation, client_order_id, authorization_id, request, timestamp):
        _aware(timestamp)
        if operation not in ("SUBMIT", "CANCEL", "REPLACE"): raise ValueError("unsupported mutation operation")
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO mutations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (mutation_id, operation, client_order_id, authorization_id, _request_json(request),
                     MutationState.PREPARED.value, timestamp.isoformat(), timestamp.isoformat(), None, SCHEMA_VERSION),
                )
        except sqlite3.IntegrityError as exc: raise ValueError("duplicate execution mutation") from exc
        return self.get(mutation_id)

    def transition(self, mutation_id, expected, target, timestamp, broker_order_id=None):
        _aware(timestamp)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self._connection.execute(
                    "UPDATE mutations SET state=?,updated_at=?,broker_order_id=COALESCE(?,broker_order_id) "
                    "WHERE mutation_id=? AND state=?",
                    (target.value, timestamp.isoformat(), broker_order_id, mutation_id, expected.value),
                )
                if cursor.rowcount != 1: raise ValueError("execution mutation state conflict")
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction: self._connection.execute("ROLLBACK")
                raise
        return self.get(mutation_id)

    def get(self, mutation_id):
        row = self._connection.execute(
            "SELECT mutation_id,operation,client_order_id,authorization_id,request_json,state,created_at,updated_at,broker_order_id FROM mutations WHERE mutation_id=?",
            (mutation_id,),
        ).fetchone()
        if row is None: raise ValueError("execution mutation is unknown")
        return MutationRecord(row[0], row[1], row[2], row[3], _request(row[4]), MutationState(row[5]),
                              datetime.fromisoformat(row[6]), datetime.fromisoformat(row[7]), row[8])

    @property
    def pending(self):
        rows = self._connection.execute(
            "SELECT mutation_id FROM mutations WHERE state != ? ORDER BY created_at,mutation_id",
            (MutationState.ACKNOWLEDGED.value,),
        ).fetchall()
        return tuple(self.get(row[0]) for row in rows)


def reconcile_startup(journal: DurableExecutionJournal, broker, registry, timestamp: datetime):
    """Reconcile without blind replay; ambiguous dispatched mutations fail closed."""
    _aware(timestamp)
    broker_orders = {item.client_order_id: item for item in broker.get_orders()}
    results = []
    for record in journal.pending:
        known = broker_orders.get(record.client_order_id)
        if known is not None:
            current = record
            if current.state is MutationState.PREPARED:
                current = journal.transition(current.mutation_id, current.state, MutationState.AUTHORIZED, timestamp)
            if current.state is MutationState.AUTHORIZED:
                current = journal.transition(current.mutation_id, current.state, MutationState.DISPATCHING, timestamp)
            if current.state in (MutationState.DISPATCHING, MutationState.UNRESOLVED):
                current = journal.transition(current.mutation_id, current.state, MutationState.ACKNOWLEDGED,
                                             timestamp, known.broker_order_id)
            results.append(current); continue
        if record.state is MutationState.PREPARED and record.authorization_id in registry.consumed_ids:
            record = journal.transition(record.mutation_id, MutationState.PREPARED,
                                        MutationState.AUTHORIZED, timestamp)
        if record.state is MutationState.AUTHORIZED:
            dispatching = journal.transition(record.mutation_id, MutationState.AUTHORIZED,
                                             MutationState.DISPATCHING, timestamp)
            response = _dispatch_recovery(broker, dispatching)
            results.append(journal.transition(dispatching.mutation_id, MutationState.DISPATCHING,
                                              MutationState.ACKNOWLEDGED, timestamp,
                                              response.broker_order_id)); continue
        if record.state is MutationState.DISPATCHING:
            results.append(journal.transition(record.mutation_id, MutationState.DISPATCHING,
                                              MutationState.UNRESOLVED, timestamp)); continue
        results.append(record)
    return tuple(results)


def _dispatch_recovery(broker, record):
    if record.operation == "SUBMIT" and record.request is not None:
        return broker.submit_order(record.request)
    if record.operation == "CANCEL":
        return broker.cancel_order(record.client_order_id)
    if record.operation == "REPLACE" and record.request is not None:
        return broker.replace_order(record.client_order_id, record.request)
    raise ValueError("durable mutation cannot be recovered")


def _request_json(value):
    if value is None: return None
    return json.dumps({"client_order_id": value.client_order_id, "symbol": value.symbol,
        "side": value.side.value, "order_type": value.order_type.value, "quantity": str(value.quantity),
        "limit_price": None if value.limit_price is None else str(value.limit_price),
        "stop_price": None if value.stop_price is None else str(value.stop_price),
        "time_in_force": value.time_in_force.value}, sort_keys=True, separators=(",", ":"))


def _request(payload):
    if payload is None: return None
    value = json.loads(payload)
    return BrokerOrderRequest(value["client_order_id"], value["symbol"], BrokerSide(value["side"]),
        BrokerOrderType(value["order_type"]), Decimal(value["quantity"]),
        None if value["limit_price"] is None else Decimal(value["limit_price"]),
        None if value["stop_price"] is None else Decimal(value["stop_price"]), TimeInForce(value["time_in_force"]))


def _aware(value):
    if not isinstance(value, datetime) or value.tzinfo is None: raise ValueError("timestamp must be timezone-aware")

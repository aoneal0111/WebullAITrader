from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from threading import RLock

from app.authorization.report import authorization_from_json, authorization_to_json
from app.authorization.validation import validate_for_consumption

SCHEMA_VERSION = 1


class AuthorizationRegistry:
    """Durable authorization registry with atomic compare-and-consume semantics."""

    __slots__ = ("database_path", "_connection", "_lock")

    def __init__(self, database_path: str | Path | None = None):
        if database_path is None:
            handle = tempfile.NamedTemporaryFile(prefix="webull-auth-", suffix=".sqlite3", delete=False)
            handle.close()
            database_path = handle.name
        self.database_path = str(Path(database_path).resolve())
        self._lock = RLock()
        self._connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None,
                                           check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @property
    def authorizations(self):
        rows = self._connection.execute(
            "SELECT payload FROM authorizations ORDER BY authorization_id"
        ).fetchall()
        return tuple(authorization_from_json(row[0]) for row in rows)

    @property
    def consumed_ids(self):
        rows = self._connection.execute(
            "SELECT authorization_id FROM authorizations WHERE consumed_at IS NOT NULL"
        ).fetchall()
        return frozenset(row[0] for row in rows)

    @property
    def revoked_ids(self):
        rows = self._connection.execute(
            "SELECT authorization_id FROM authorizations WHERE revoked_at IS NOT NULL"
        ).fetchall()
        return frozenset(row[0] for row in rows)

    def _migrate(self) -> None:
        with self._connection:
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS registry_metadata (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    consumed_at TEXT,
                    revoked_at TEXT,
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_state (
                    approval_id TEXT PRIMARY KEY,
                    revoked_at TEXT,
                    superseded_at TEXT,
                    schema_version INTEGER NOT NULL
                );
            """)
            row = self._connection.execute(
                "SELECT value FROM registry_metadata WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO registry_metadata(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row[0]) != SCHEMA_VERSION:
                raise ValueError("unsupported authorization registry schema")


def _register_issued(registry, authorization):
    _require_registry(registry)
    payload = authorization_to_json(authorization)
    try:
        with registry._lock, registry._connection:
            registry._connection.execute(
                "INSERT INTO authorizations"
                "(authorization_id,payload,issued_at,schema_version) VALUES(?,?,?,?)",
                (authorization.authorization_id, payload, authorization.issued_at.isoformat(), SCHEMA_VERSION),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("authorization identity already exists") from exc
    return registry


def revoke(registry, authorization_id, timestamp: datetime | None = None):
    known = _known(registry, authorization_id)
    when = _timestamp(timestamp or known.issued_at)
    with registry._lock, registry._connection:
        registry._connection.execute(
            "UPDATE authorizations SET revoked_at=? WHERE authorization_id=?",
            (when, authorization_id),
        )
    return registry


def revoke_evidence(registry, approval_id, timestamp: datetime | None = None):
    return _set_evidence(registry, approval_id, "revoked_at", timestamp)


def supersede_evidence(registry, approval_id, timestamp: datetime | None = None):
    return _set_evidence(registry, approval_id, "superseded_at", timestamp)


def validate_known(registry, intent, authorization, now):
    _require_registry(registry)
    known = _known(registry, authorization.authorization_id)
    if known != authorization:
        raise ValueError("authorization identity substitution detected")
    row = registry._connection.execute(
        "SELECT consumed_at,revoked_at FROM authorizations WHERE authorization_id=?",
        (authorization.authorization_id,),
    ).fetchone()
    if row[1] is not None:
        raise ValueError("authorization is revoked")
    if authorization.single_use and row[0] is not None:
        raise ValueError("authorization is already consumed")
    _validate_evidence_state(registry, authorization)
    validate_for_consumption(intent, authorization, now)


def consume(registry, intent, authorization, now):
    """Atomically validate current durable state and consume exactly once."""
    _require_registry(registry)
    validate_for_consumption(intent, authorization, now)
    with registry._lock:
        connection = registry._connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            known = _known(registry, authorization.authorization_id)
            if known != authorization:
                raise ValueError("authorization identity substitution detected")
            _validate_evidence_state(registry, authorization)
            row = connection.execute(
                "SELECT consumed_at,revoked_at FROM authorizations WHERE authorization_id=?",
                (authorization.authorization_id,),
            ).fetchone()
            if row[1] is not None:
                raise ValueError("authorization is revoked")
            if authorization.single_use and row[0] is not None:
                raise ValueError("authorization is already consumed")
            if authorization.single_use:
                cursor = connection.execute(
                    "UPDATE authorizations SET consumed_at=? "
                    "WHERE authorization_id=? AND consumed_at IS NULL AND revoked_at IS NULL",
                    (_timestamp(now), authorization.authorization_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("authorization is already consumed or revoked")
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    return registry


def _known(registry, authorization_id):
    _require_registry(registry)
    row = registry._connection.execute(
        "SELECT payload FROM authorizations WHERE authorization_id=?", (authorization_id,)
    ).fetchone()
    if row is None:
        raise ValueError("authorization identity is unknown")
    return authorization_from_json(row[0])


def _set_evidence(registry, approval_id, column, timestamp):
    _require_registry(registry)
    if not isinstance(approval_id, str) or not approval_id.strip():
        raise ValueError("approval identity is required")
    when = _timestamp(timestamp or datetime.now().astimezone())
    other = "superseded_at" if column == "revoked_at" else "revoked_at"
    with registry._lock, registry._connection:
        registry._connection.execute(
            f"INSERT INTO evidence_state(approval_id,{column},{other},schema_version) VALUES(?,?,NULL,?) "
            f"ON CONFLICT(approval_id) DO UPDATE SET {column}=excluded.{column}",
            (approval_id, when, SCHEMA_VERSION),
        )
    return registry


def _validate_evidence_state(registry, authorization):
    for approval_id in (authorization.risk_approval_id, authorization.compliance_approval_id):
        row = registry._connection.execute(
            "SELECT revoked_at,superseded_at FROM evidence_state WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row and row[0] is not None:
            raise ValueError("authorization evidence is revoked")
        if row and row[1] is not None:
            raise ValueError("authorization evidence is superseded")


def _timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat()


def _require_registry(value):
    if not isinstance(value, AuthorizationRegistry):
        raise ValueError("AuthorizationRegistry is required")

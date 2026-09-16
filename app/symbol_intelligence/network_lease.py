"""Machine-local, bounded lease for SEC network ownership.

This module deliberately has no runtime or network integration.  It provides
an atomic SQLite sidecar primitive for a later SEC activation phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
import platform
import sqlite3
from threading import Lock
import uuid


LEASE_NAME = "atlas-sec-edgar-network-owner-v1"
SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 45.0
MIN_TTL_SECONDS = 10.0
MAX_TTL_SECONDS = 300.0
DEFAULT_BUSY_TIMEOUT_MS = 2_000


class LeaseOutcome(StrEnum):
    ACQUIRED = "ACQUIRED"
    RENEWED = "RENEWED"
    DENIED = "DENIED"
    TAKEN_OVER_EXPIRED = "TAKEN_OVER_EXPIRED"
    ERROR = "ERROR"


class LeaseSchemaError(RuntimeError):
    """The sidecar schema is missing, corrupt, or newer than supported."""


@dataclass(frozen=True, slots=True)
class LeaseResult:
    outcome: LeaseOutcome
    is_owner: bool
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LeaseMetrics:
    acquire_attempts: int = 0
    acquire_successes: int = 0
    renewals: int = 0
    denials: int = 0
    takeovers: int = 0
    heartbeats: int = 0
    heartbeat_failures: int = 0
    releases: int = 0
    release_failures: int = 0
    sqlite_busy_failures: int = 0
    internal_errors: int = 0


@dataclass(frozen=True, slots=True)
class LeaseDiagnostics:
    lease_name: str
    owner_id: str
    is_owner: bool
    expires_at: datetime | None
    last_heartbeat: datetime | None
    metrics: LeaseMetrics
    last_error_category: str | None = None


def default_lease_path() -> Path:
    return Path("data") / "symbol_intelligence" / "network" / "atlas_sec_edgar_lease.sqlite3"


class SecNetworkOwnershipLease:
    """Atomic, machine-wide ownership lease backed by one SQLite row."""

    def __init__(
        self,
        path: str | Path,
        *,
        lease_name: str = LEASE_NAME,
        owner_id: str | None = None,
        pid: int | None = None,
        host_id: str | None = None,
        process_started_at: datetime | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        if not str(lease_name).strip() or len(str(lease_name)) > 128:
            raise ValueError("lease_name is required and bounded")
        if not MIN_TTL_SECONDS <= float(ttl_seconds) <= MAX_TTL_SECONDS:
            raise ValueError("ttl_seconds must be in 10..300")
        if busy_timeout_ms <= 0 or busy_timeout_ms > 30_000:
            raise ValueError("busy_timeout_ms must be in 1..30000")
        self.path = Path(path)
        self.lease_name = str(lease_name).strip()
        self.owner_id = str(owner_id or uuid.uuid4())
        if not self.owner_id or len(self.owner_id) > 128:
            raise ValueError("owner_id is malformed")
        self.pid = int(os.getpid() if pid is None else pid)
        self.host_id = host_id or _host_id()
        if len(self.host_id) > 128:
            raise ValueError("host_id is malformed")
        self.process_started_at = _utc(process_started_at or clock())
        self.ttl_seconds = float(ttl_seconds)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._clock = clock
        self._lock = Lock()
        self._metrics = LeaseMetrics()
        self._expires_at: datetime | None = None
        self._last_heartbeat: datetime | None = None
        self._is_owner = False
        self._last_error_category: str | None = None

    def initialize(self) -> None:
        """Create/validate the dedicated schema; safe for concurrent callers."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS lease_metadata "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT value FROM lease_metadata WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                existing = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='leases'"
                ).fetchone()[0]
                if existing:
                    raise LeaseSchemaError("lease schema version is missing")
                connection.execute(
                    "INSERT INTO lease_metadata(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),),
                )
            else:
                try:
                    version = int(row[0])
                except (TypeError, ValueError) as exc:
                    raise LeaseSchemaError("lease schema version is malformed") from exc
                if version > SCHEMA_VERSION:
                    raise LeaseSchemaError("lease schema is newer than supported")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS leases ("
                "lease_name TEXT PRIMARY KEY, owner_id TEXT NOT NULL, pid INTEGER NOT NULL,"
                "host_id TEXT NOT NULL, process_started_at TEXT NOT NULL, acquired_at TEXT NOT NULL,"
                "heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL, schema_version INTEGER NOT NULL)"
            )

    def try_acquire(self) -> LeaseResult:
        self._inc("acquire_attempts")
        now = _utc(self._clock())
        try:
            self.initialize()
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT owner_id,expires_at,heartbeat_at FROM leases WHERE lease_name=?",
                    (self.lease_name,),
                ).fetchone()
                if row is not None:
                    expires = _parse_time(row[1])
                    if row[0] == self.owner_id and now < _parse_time(row[2]):
                        self._error("BACKWARDS_CLOCK")
                        return LeaseResult(LeaseOutcome.ERROR, False, expires)
                    if row[0] == self.owner_id and now < expires:
                        expiry = now + timedelta(seconds=self.ttl_seconds)
                        connection.execute(
                            "UPDATE leases SET heartbeat_at=?,expires_at=? WHERE lease_name=? AND owner_id=?",
                            (_stamp(now), _stamp(expiry), self.lease_name, self.owner_id),
                        )
                        self._set_owned(expiry, now)
                        self._inc("renewals")
                        return LeaseResult(LeaseOutcome.RENEWED, True, expiry)
                    if now < expires:
                        self._is_owner = False
                        self._inc("denials")
                        return LeaseResult(LeaseOutcome.DENIED, False, expires)
                    outcome = LeaseOutcome.TAKEN_OVER_EXPIRED
                    self._inc("takeovers")
                else:
                    outcome = LeaseOutcome.ACQUIRED
                expiry = now + timedelta(seconds=self.ttl_seconds)
                connection.execute(
                    "INSERT INTO leases(lease_name,owner_id,pid,host_id,process_started_at,acquired_at,heartbeat_at,expires_at,schema_version) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(lease_name) DO UPDATE SET owner_id=excluded.owner_id,pid=excluded.pid,host_id=excluded.host_id,process_started_at=excluded.process_started_at,acquired_at=excluded.acquired_at,heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at,schema_version=excluded.schema_version",
                    (self.lease_name, self.owner_id, self.pid, self.host_id, _stamp(self.process_started_at), _stamp(now), _stamp(now), _stamp(expiry), SCHEMA_VERSION),
                )
                self._set_owned(expiry, now)
                self._inc("acquire_successes")
                return LeaseResult(outcome, True, expiry)
        except LeaseSchemaError:
            self._error("SCHEMA_ERROR")
            raise
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                self._inc("sqlite_busy_failures")
                self._error("SQLITE_BUSY")
            else:
                self._error("SQLITE_ERROR")
            return LeaseResult(LeaseOutcome.ERROR, False)
        except Exception:
            self._error("INTERNAL_ERROR")
            return LeaseResult(LeaseOutcome.ERROR, False)

    def heartbeat(self) -> LeaseResult:
        now = _utc(self._clock())
        try:
            self.initialize()
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT owner_id,heartbeat_at,expires_at FROM leases WHERE lease_name=?", (self.lease_name,)).fetchone()
                if row is None or row[0] != self.owner_id:
                    self._heartbeat_failed("NOT_OWNER")
                    return LeaseResult(LeaseOutcome.DENIED, False)
                previous = _parse_time(row[1])
                if now < previous or now >= _parse_time(row[2]):
                    self._heartbeat_failed("LEASE_INVALID")
                    return LeaseResult(LeaseOutcome.DENIED, False)
                expiry = now + timedelta(seconds=self.ttl_seconds)
                connection.execute("UPDATE leases SET heartbeat_at=?,expires_at=? WHERE lease_name=? AND owner_id=?", (_stamp(now), _stamp(expiry), self.lease_name, self.owner_id))
                self._set_owned(expiry, now)
                self._inc("heartbeats")
                return LeaseResult(LeaseOutcome.RENEWED, True, expiry)
        except LeaseSchemaError:
            self._error("SCHEMA_ERROR")
            raise
        except Exception:
            self._heartbeat_failed("INTERNAL_ERROR")
            return LeaseResult(LeaseOutcome.ERROR, False)

    def release(self) -> bool:
        try:
            self.initialize()
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute("DELETE FROM leases WHERE lease_name=? AND owner_id=?", (self.lease_name, self.owner_id))
                removed = cursor.rowcount == 1
            with self._lock:
                if removed:
                    self._inc_locked("releases")
                else:
                    self._inc_locked("release_failures")
                self._is_owner = False
                self._expires_at = None
            return removed
        except LeaseSchemaError:
            self._error("SCHEMA_ERROR")
            raise
        except Exception:
            self._inc("release_failures")
            self._error("INTERNAL_ERROR")
            return False

    def diagnostics(self) -> LeaseDiagnostics:
        with self._lock:
            return LeaseDiagnostics(self.lease_name, self.owner_id, self._is_owner, self._expires_at, self._last_heartbeat, self._metrics, self._last_error_category)

    @property
    def metrics(self) -> LeaseMetrics:
        with self._lock:
            return self._metrics

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1_000)
        try:
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _set_owned(self, expiry: datetime, heartbeat: datetime) -> None:
        with self._lock:
            self._is_owner, self._expires_at, self._last_heartbeat = True, expiry, heartbeat

    def _heartbeat_failed(self, category: str) -> None:
        with self._lock:
            self._is_owner = False
            self._inc_locked("heartbeat_failures")
            self._last_error_category = category

    def _error(self, category: str) -> None:
        with self._lock:
            self._is_owner = False
            self._inc_locked("internal_errors")
            self._last_error_category = category

    def _inc(self, field: str, amount: int = 1) -> None:
        with self._lock:
            self._inc_locked(field, amount)

    def _inc_locked(self, field: str, amount: int = 1) -> None:
        values = {name: getattr(self._metrics, name) for name in self._metrics.__dataclass_fields__}
        values[field] += amount
        self._metrics = LeaseMetrics(**values)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lease clock must return timezone-aware datetime")
    return value.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse_time(value: object) -> datetime:
    return _utc(datetime.fromisoformat(str(value)))


def _host_id() -> str:
    raw = platform.node().strip() or "unknown-host"
    return "host-" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


__all__ = [
    "DEFAULT_BUSY_TIMEOUT_MS", "DEFAULT_TTL_SECONDS", "LEASE_NAME", "MAX_TTL_SECONDS",
    "MIN_TTL_SECONDS", "SCHEMA_VERSION", "LeaseDiagnostics", "LeaseMetrics", "LeaseOutcome",
    "LeaseResult", "LeaseSchemaError", "SecNetworkOwnershipLease", "default_lease_path",
]

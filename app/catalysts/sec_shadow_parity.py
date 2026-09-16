"""Offline SEC catalyst shadow comparison and bounded diagnostics."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from threading import Lock
from typing import Callable
import sqlite3

from app.momentum_scanner.models import CatalystStatus

from .models import CatalystEvidence


class ParityState(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    SHADOW_NOT_READY = "SHADOW_NOT_READY"
    SHADOW_ERROR = "SHADOW_ERROR"


class MismatchKind(StrEnum):
    CRITICAL_STATUS_CONTRADICTION = "CRITICAL_STATUS_CONTRADICTION"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    CATALYST_TYPE_MISMATCH = "CATALYST_TYPE_MISMATCH"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    EVENT_ID_MISMATCH = "EVENT_ID_MISMATCH"
    PUBLISHED_AT_MISMATCH = "PUBLISHED_AT_MISMATCH"
    SOURCE_URL_MISMATCH = "SOURCE_URL_MISMATCH"
    HEADLINE_MISMATCH = "HEADLINE_MISMATCH"


class ReadinessReason(StrEnum):
    SOURCE_STATE_MISSING = "SOURCE_STATE_MISSING"
    SOURCE_UNAVAILABLE_PREACTIVATION = "SOURCE_UNAVAILABLE_PREACTIVATION"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    DATA_ABSENT = "DATA_ABSENT"
    HISTORICAL_SOURCE_HEALTH_UNKNOWN = "HISTORICAL_SOURCE_HEALTH_UNKNOWN"


@dataclass(frozen=True, slots=True)
class SecCatalystParityObservation:
    schema_version: int
    environment: str
    symbol: str
    observed_at: datetime
    state: ParityState
    mismatch_kinds: tuple[MismatchKind, ...] = ()
    legacy_status: CatalystStatus = CatalystStatus.UNKNOWN
    shadow_status: CatalystStatus = CatalystStatus.UNKNOWN
    legacy_canonical_event_id: str | None = None
    shadow_canonical_event_id: str | None = None
    legacy_provider_event_id: str | None = None
    shadow_provider_event_id: str | None = None
    readiness_reason: ReadinessReason | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported parity observation schema")
        if not self.environment.strip() or not self.symbol.strip():
            raise ValueError("environment and symbol are required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "environment", self.environment.strip().upper())
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        object.__setattr__(self, "mismatch_kinds", tuple(self.mismatch_kinds))


@dataclass(frozen=True, slots=True)
class ParityMetrics:
    evaluations: int = 0
    matches: int = 0
    mismatches: int = 0
    shadow_not_ready: int = 0
    shadow_errors: int = 0
    critical_status_contradictions: int = 0
    event_id_mismatches: int = 0
    published_at_mismatches: int = 0
    source_url_mismatches: int = 0
    headline_mismatches: int = 0
    storage_failures: int = 0
    deduplicated_observations: int = 0


@dataclass(frozen=True, slots=True)
class ParitySummary:
    total: int
    eligible: int
    matches: int
    mismatches: int
    not_ready: int
    errors: int
    critical_status_contradictions: int
    event_id_mismatches: int
    published_at_mismatches: int
    source_url_mismatches: int
    headline_mismatches: int
    examples: tuple[SecCatalystParityObservation, ...] = ()


def compare_sec_catalyst_evidence(
    *,
    symbol: str,
    observed_at: datetime,
    environment: str,
    legacy: CatalystEvidence,
    shadow: CatalystEvidence,
    shadow_ready: bool,
    readiness_reason: ReadinessReason | None = None,
) -> SecCatalystParityObservation:
    """Pure, deterministic comparison of two consumer-facing evidence values."""

    if not isinstance(legacy, CatalystEvidence) or not isinstance(shadow, CatalystEvidence):
        raise TypeError("legacy and shadow must be CatalystEvidence")
    observed = _utc(observed_at)
    if not shadow_ready:
        return SecCatalystParityObservation(
            1, environment, symbol, observed, ParityState.SHADOW_NOT_READY,
            legacy_status=legacy.status, shadow_status=shadow.status,
            legacy_canonical_event_id=legacy.canonical_event_id,
            shadow_canonical_event_id=shadow.canonical_event_id,
            legacy_provider_event_id=legacy.provider_event_id,
            shadow_provider_event_id=shadow.provider_event_id,
            readiness_reason=readiness_reason,
        )

    kinds: list[MismatchKind] = []
    if legacy.status is not shadow.status:
        kinds.append(MismatchKind.STATUS_MISMATCH)
        if (
            (legacy.status is CatalystStatus.TRUE and shadow.status in {CatalystStatus.FALSE, CatalystStatus.UNKNOWN, CatalystStatus.UNAVAILABLE})
            or (legacy.status is CatalystStatus.FALSE and shadow.status is CatalystStatus.TRUE)
        ):
            kinds.append(MismatchKind.CRITICAL_STATUS_CONTRADICTION)
    if legacy.catalyst_type is not shadow.catalyst_type:
        kinds.append(MismatchKind.CATALYST_TYPE_MISMATCH)
    if legacy.source != shadow.source:
        kinds.append(MismatchKind.SOURCE_MISMATCH)
    if legacy.status is CatalystStatus.TRUE and shadow.status is CatalystStatus.TRUE:
        if legacy.provider_event_id != shadow.provider_event_id or legacy.canonical_event_id != shadow.canonical_event_id:
            kinds.append(MismatchKind.EVENT_ID_MISMATCH)
        if _optional_utc(legacy.published_at) != _optional_utc(shadow.published_at):
            kinds.append(MismatchKind.PUBLISHED_AT_MISMATCH)
        if legacy.source_url != shadow.source_url:
            kinds.append(MismatchKind.SOURCE_URL_MISMATCH)
        if legacy.headline != shadow.headline:
            kinds.append(MismatchKind.HEADLINE_MISMATCH)
    state = ParityState.MISMATCH if kinds else ParityState.MATCH
    return SecCatalystParityObservation(
        1, environment, symbol, observed, state, tuple(dict.fromkeys(kinds)),
        legacy_status=legacy.status, shadow_status=shadow.status,
        legacy_canonical_event_id=legacy.canonical_event_id,
        shadow_canonical_event_id=shadow.canonical_event_id,
        legacy_provider_event_id=legacy.provider_event_id,
        shadow_provider_event_id=shadow.provider_event_id,
    )


class SecShadowMetrics:
    """Thread-safe bounded counters for shadow evaluations."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._values = ParityMetrics()

    def snapshot(self) -> ParityMetrics:
        with self._lock:
            return self._values

    def increment(self, **changes: int) -> None:
        with self._lock:
            values = {field: getattr(self._values, field) for field in self._values.__dataclass_fields__}
            for key, amount in changes.items():
                if key in values:
                    values[key] += int(amount)
            self._values = ParityMetrics(**values)


class SecCatalystParityStore:
    """Dedicated bounded SQLite diagnostics store; never stores raw evidence."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str, *, max_rows: int = 25_000, max_rows_per_symbol: int = 128,
                 retention_days: int = 30, busy_timeout_ms: int = 2_000) -> None:
        if max_rows <= 0 or max_rows_per_symbol <= 0 or retention_days <= 0 or busy_timeout_ms <= 0:
            raise ValueError("store bounds must be positive")
        self.path = str(path)
        self.max_rows = max_rows
        self.max_rows_per_symbol = max_rows_per_symbol
        self.retention_days = retention_days
        self.busy_timeout_ms = busy_timeout_ms
        self._initialize()

    @staticmethod
    def production_path(environment: str) -> str:
        normalized = str(environment).strip().upper()
        if normalized not in {"TEST", "PAPER", "SANDBOX", "LIVE", "PRODUCTION"}:
            raise ValueError("unsupported environment")
        from pathlib import Path
        return str(Path("data") / "symbol_intelligence" / normalized.casefold() / "sec_shadow_parity.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return connection

    def _initialize(self) -> None:
        db = self._connect()
        try:
          with db:
            db.executescript("CREATE TABLE IF NOT EXISTS repository_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS parity_observations ("
                             "id INTEGER PRIMARY KEY, observation_key TEXT NOT NULL UNIQUE, observed_at TEXT NOT NULL, "
                             "environment TEXT NOT NULL, symbol TEXT NOT NULL, state TEXT NOT NULL, legacy_status TEXT NOT NULL, "
                             "shadow_status TEXT NOT NULL, legacy_canonical_event_id TEXT, shadow_canonical_event_id TEXT, "
                             "legacy_provider_event_id TEXT, shadow_provider_event_id TEXT, mismatch_kinds TEXT NOT NULL, readiness_reason TEXT);"
                             "CREATE INDEX IF NOT EXISTS ix_parity_observed_at ON parity_observations(observed_at);"
                             "CREATE INDEX IF NOT EXISTS ix_parity_symbol_observed ON parity_observations(symbol, observed_at);")
            row = db.execute("SELECT value FROM repository_metadata WHERE key='schema_version'").fetchone()
            if row is None:
                db.execute("INSERT INTO repository_metadata(key,value) VALUES('schema_version','1')")
            elif int(row[0]) != self.SCHEMA_VERSION:
                raise RuntimeError("unsupported parity store schema")
        finally:
            db.close()

    def record(self, observation: SecCatalystParityObservation) -> bool:
        key = _observation_key(observation)
        cutoff = (observation.observed_at - timedelta(days=self.retention_days)).isoformat()
        db = self._connect()
        try:
          with db:
            db.execute("DELETE FROM parity_observations WHERE observed_at < ?", (cutoff,))
            cursor = db.execute(
                "INSERT OR IGNORE INTO parity_observations(observation_key,observed_at,environment,symbol,state,legacy_status,shadow_status,legacy_canonical_event_id,shadow_canonical_event_id,legacy_provider_event_id,shadow_provider_event_id,mismatch_kinds,readiness_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, observation.observed_at.isoformat(), observation.environment, observation.symbol, observation.state.value,
                 observation.legacy_status.value, observation.shadow_status.value, observation.legacy_canonical_event_id,
                 observation.shadow_canonical_event_id, observation.legacy_provider_event_id, observation.shadow_provider_event_id,
                 ",".join(item.value for item in observation.mismatch_kinds),
                 observation.readiness_reason.value if observation.readiness_reason else None),
            )
            db.execute("DELETE FROM parity_observations WHERE id IN (SELECT id FROM parity_observations WHERE symbol=? ORDER BY observed_at DESC,id DESC LIMIT -1 OFFSET ?)", (observation.symbol, self.max_rows_per_symbol))
            db.execute("DELETE FROM parity_observations WHERE id IN (SELECT id FROM parity_observations ORDER BY observed_at DESC,id DESC LIMIT -1 OFFSET ?)", (self.max_rows,))
            return cursor.rowcount == 1
        finally:
            db.close()

    def summary(self, *, since: datetime, limit_examples: int = 20) -> ParitySummary:
        if limit_examples < 0 or limit_examples > 50:
            raise ValueError("limit_examples must be in 0..50")
        cutoff = _utc(since).isoformat()
        db = self._connect()
        try:
          with db:
            rows = db.execute("SELECT state,mismatch_kinds FROM parity_observations WHERE observed_at >= ?", (cutoff,)).fetchall()
            examples = db.execute("SELECT * FROM parity_observations WHERE observed_at >= ? AND state='MISMATCH' ORDER BY observed_at DESC,id DESC LIMIT ?", (cutoff, limit_examples)).fetchall()
        finally:
            db.close()
        counts = {state.value: 0 for state in ParityState}
        kind_counts = {kind.value: 0 for kind in MismatchKind}
        for row in rows:
            counts[row[0]] += 1
            for value in filter(None, str(row[1]).split(',')):
                if value in kind_counts:
                    kind_counts[value] += 1
        return ParitySummary(
            total=len(rows), eligible=counts[ParityState.MATCH.value] + counts[ParityState.MISMATCH.value],
            matches=counts[ParityState.MATCH.value], mismatches=counts[ParityState.MISMATCH.value],
            not_ready=counts[ParityState.SHADOW_NOT_READY.value], errors=counts[ParityState.SHADOW_ERROR.value],
            critical_status_contradictions=kind_counts[MismatchKind.CRITICAL_STATUS_CONTRADICTION.value],
            event_id_mismatches=kind_counts[MismatchKind.EVENT_ID_MISMATCH.value],
            published_at_mismatches=kind_counts[MismatchKind.PUBLISHED_AT_MISMATCH.value],
            source_url_mismatches=kind_counts[MismatchKind.SOURCE_URL_MISMATCH.value],
            headline_mismatches=kind_counts[MismatchKind.HEADLINE_MISMATCH.value],
            examples=tuple(_row_observation(row) for row in examples),
        )


class SecCatalystShadowEvaluator:
    """Safely evaluates shadow evidence while returning legacy evidence verbatim."""

    def __init__(self, adapter: object, *, store: SecCatalystParityStore | None = None,
                 environment: str = "PAPER", clock: Callable[[], datetime] | None = None,
                 metrics: SecShadowMetrics | None = None, max_dedupe_keys: int = 4096,
                 bucket_seconds: int = 60) -> None:
        if max_dedupe_keys <= 0 or bucket_seconds <= 0:
            raise ValueError("dedupe bounds must be positive")
        self.adapter, self.store = adapter, store
        self.environment = environment.strip().upper()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.metrics = metrics or SecShadowMetrics()
        self._max_dedupe_keys = max_dedupe_keys
        self._bucket_seconds = bucket_seconds
        self._dedupe: OrderedDict[str, None] = OrderedDict()
        self._lock = Lock()

    def evaluate(self, symbol: str, *, as_of: datetime, legacy_evidence: CatalystEvidence,
                 shadow_ready: bool = True, readiness_reason: ReadinessReason | None = None) -> CatalystEvidence:
        self.metrics.increment(evaluations=1)
        try:
            shadow = self.adapter.get_evidence(symbol, as_of=as_of)
            observation = compare_sec_catalyst_evidence(
                symbol=symbol, observed_at=self.clock(), environment=self.environment,
                legacy=legacy_evidence, shadow=shadow, shadow_ready=shadow_ready,
                readiness_reason=readiness_reason,
            )
        except Exception:
            self.metrics.increment(shadow_errors=1)
            try:
                self._record_error(symbol, legacy_evidence, readiness_reason)
            except Exception:
                self.metrics.increment(storage_failures=1)
            return legacy_evidence
        self._count(observation)
        try:
            self._record(observation)
        except Exception:
            self.metrics.increment(storage_failures=1)
        return legacy_evidence

    def _record_error(self, symbol: str, legacy: CatalystEvidence,
                      readiness_reason: ReadinessReason | None) -> None:
        if self.store is None:
            return
        observation = SecCatalystParityObservation(
            1, self.environment, symbol, _utc(self.clock()), ParityState.SHADOW_ERROR,
            legacy_status=legacy.status, shadow_status=CatalystStatus.UNKNOWN,
            legacy_canonical_event_id=legacy.canonical_event_id,
            legacy_provider_event_id=legacy.provider_event_id,
            readiness_reason=readiness_reason,
        )
        self._record(observation)

    def _count(self, observation: SecCatalystParityObservation) -> None:
        increments = {"matches": observation.state is ParityState.MATCH,
                      "mismatches": observation.state is ParityState.MISMATCH,
                      "shadow_not_ready": observation.state is ParityState.SHADOW_NOT_READY}
        for kind, field in ((MismatchKind.CRITICAL_STATUS_CONTRADICTION, "critical_status_contradictions"),
                            (MismatchKind.EVENT_ID_MISMATCH, "event_id_mismatches"),
                            (MismatchKind.PUBLISHED_AT_MISMATCH, "published_at_mismatches"),
                            (MismatchKind.SOURCE_URL_MISMATCH, "source_url_mismatches"),
                            (MismatchKind.HEADLINE_MISMATCH, "headline_mismatches")):
            increments[field] = kind in observation.mismatch_kinds
        self.metrics.increment(**{key: int(value) for key, value in increments.items()})

    def _record(self, observation: SecCatalystParityObservation) -> None:
        if self.store is None:
            return
        key = _observation_key(observation, bucket_seconds=self._bucket_seconds)
        with self._lock:
            if key in self._dedupe:
                self._dedupe.move_to_end(key)
                self.metrics.increment(deduplicated_observations=1)
                return
            self._dedupe[key] = None
            if len(self._dedupe) > self._max_dedupe_keys:
                self._dedupe.popitem(last=False)
        if not self.store.record(observation):
            self.metrics.increment(deduplicated_observations=1)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return None if value is None else _utc(value)


def _observation_key(item: SecCatalystParityObservation, *, bucket_seconds: int = 60) -> str:
    bucket = int(item.observed_at.timestamp()) // bucket_seconds
    raw = "|".join((item.environment, item.symbol, item.legacy_status.value, item.legacy_canonical_event_id or "",
                    item.shadow_status.value, item.shadow_canonical_event_id or "", str(bucket)))
    return sha256(raw.encode()).hexdigest()


def _row_observation(row: sqlite3.Row) -> SecCatalystParityObservation:
    return SecCatalystParityObservation(
        1, row["environment"], row["symbol"], datetime.fromisoformat(row["observed_at"]),
        ParityState(row["state"]), tuple(MismatchKind(value) for value in filter(None, row["mismatch_kinds"].split(","))),
        CatalystStatus(row["legacy_status"]), CatalystStatus(row["shadow_status"]),
        row["legacy_canonical_event_id"], row["shadow_canonical_event_id"],
        row["legacy_provider_event_id"], row["shadow_provider_event_id"],
        ReadinessReason(row["readiness_reason"]) if row["readiness_reason"] else None,
    )


__all__ = [
    "MismatchKind", "ParityMetrics", "ParityState", "ParitySummary", "ReadinessReason",
    "SecCatalystParityObservation", "SecCatalystParityStore", "SecCatalystShadowEvaluator",
    "SecShadowMetrics", "compare_sec_catalyst_evidence",
]

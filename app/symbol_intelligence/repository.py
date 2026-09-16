"""Versioned, bounded SQLite persistence for symbol intelligence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any
from itertools import islice
import json
import sqlite3

from .models import (
    ATTENTION_RANK,
    AttentionState,
    CatalystSummary,
    DecayClass,
    Direction,
    EventType,
    IngestResult,
    IntelligenceDerivation,
    IntelligenceEvent,
    SourceAvailability,
    SourceStateSnapshot,
    SymbolAlias,
    SymbolIdentity,
    SymbolIntelligenceSnapshot,
)
from .security import UnsafePayloadError, safe_json_value, validate_safe_key, validate_safe_text


REPOSITORY_SCHEMA_VERSION = 3
DEFAULT_STARTUP_RECOVERY_MAX = 5_000
DEFAULT_SNAPSHOT_RECOVERY_MAX = 4_096
MAX_QUERY_LIMIT = 5_000
MAX_WRITE_BATCH = 1_000


class RepositorySchemaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RepositoryBounds:
    raw_events: int = 250_000
    active_event_states: int = 32_768
    episode_summaries: int = 100_000
    snapshots: int = 32_768
    derivations: int = 250_000
    source_states: int = 64
    symbol_identities: int = 32_768
    symbol_aliases: int = 65_536
    repository_metadata: int = 128

    def __post_init__(self) -> None:
        if min(
            self.raw_events, self.active_event_states, self.episode_summaries,
            self.snapshots, self.derivations,
            self.source_states, self.symbol_identities, self.symbol_aliases,
            self.repository_metadata,
        ) <= 0:
            raise ValueError("repository bounds must be positive")


@dataclass(frozen=True, slots=True)
class RepositoryMetrics:
    table_row_counts: tuple[tuple[str, int], ...]
    database_bytes: int
    wal_bytes: int
    events_inserted: int
    events_deduplicated: int
    events_rejected: int
    recovery_rows: int
    recovery_milliseconds: float
    sqlite_busy_or_locked: int


_COUNTED_TABLES = (
    "repository_metadata", "source_states", "symbol_identities", "symbol_aliases", "raw_events",
    "event_derivations", "active_event_states", "symbol_episode_summaries",
    "symbol_snapshots",
)


class SymbolIntelligenceRepository:
    """Per-operation connections make the documented API cross-thread safe."""

    def __init__(
        self,
        path: str | Path,
        *,
        bounds: RepositoryBounds = RepositoryBounds(),
        busy_timeout_ms: int = 2_000,
    ) -> None:
        self.path = Path(path)
        if busy_timeout_ms <= 0 or busy_timeout_ms > 30_000:
            raise ValueError("busy_timeout_ms must be in 1..30000")
        self.bounds = bounds
        self._busy_timeout_ms = busy_timeout_ms
        self._metrics_lock = Lock()
        self._events_inserted = self._events_deduplicated = self._events_rejected = 0
        self._recovery_rows = 0
        self._recovery_milliseconds = 0.0
        self._sqlite_busy_or_locked = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def production_path(environment: str) -> Path:
        normalized = str(environment).strip().upper()
        if normalized not in {"TEST", "PAPER", "SANDBOX", "LIVE", "PRODUCTION"}:
            raise ValueError("unsupported repository environment")
        return Path("data") / "symbol_intelligence" / normalized.casefold() / "symbol_intelligence.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self._busy_timeout_ms / 1_000)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            with connection:
                self._initialize_connection(connection)
        finally:
            connection.close()

    def _initialize_connection(self, connection: sqlite3.Connection) -> None:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='repository_metadata'"
        ).fetchone()
        if exists:
            row = connection.execute(
                "SELECT value FROM repository_metadata WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                raise RepositorySchemaError("repository schema version is missing")
            try:
                version = int(row[0])
            except (TypeError, ValueError) as exc:
                raise RepositorySchemaError("repository schema version is malformed") from exc
            if version > REPOSITORY_SCHEMA_VERSION:
                raise RepositorySchemaError(
                    f"repository schema {version} is newer than supported {REPOSITORY_SCHEMA_VERSION}"
                )
            base_tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            required_base = {
                "repository_metadata", "source_states", "symbol_identities", "symbol_aliases",
                "raw_events", "event_derivations", "active_event_states",
                "symbol_episode_summaries", "symbol_snapshots",
            }
            if not required_base.issubset(base_tables):
                raise RepositorySchemaError("repository schema is incomplete")
            self._migrate_forward(connection, version)
            tables = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = set(_COUNTED_TABLES) - tables
            if missing:
                raise RepositorySchemaError("repository schema is incomplete")
            return
        connection.executescript(_SCHEMA)
        connection.execute(
            "INSERT INTO repository_metadata(key,value) VALUES('schema_version',?)",
            (str(REPOSITORY_SCHEMA_VERSION),),
        )

    @staticmethod
    def _migrate_forward(connection: sqlite3.Connection, version: int) -> None:
        if version > REPOSITORY_SCHEMA_VERSION:
            raise RepositorySchemaError(
                f"repository schema {version} is newer than supported {REPOSITORY_SCHEMA_VERSION}"
            )
        migrations: dict[int, Any] = {1: _migrate_v1_to_v2, 2: _migrate_v2_to_v3}
        while version < REPOSITORY_SCHEMA_VERSION:
            migration = migrations.get(version)
            if migration is None:
                raise RepositorySchemaError(f"no forward migration from repository schema {version}")
            migration(connection)
            version += 1
            connection.execute(
                "UPDATE repository_metadata SET value=? WHERE key='schema_version'",
                (str(version),),
            )

    def apply_sec_ticker_map(self, ticker_map: Any) -> bool:
        """Atomically apply a pre-validated offline SEC ticker map."""
        from .providers.sec_identity import SecTickerMap
        if not isinstance(ticker_map, SecTickerMap):
            raise TypeError("ticker_map must be SecTickerMap")
        identities = ticker_map.identities
        if len(identities) > self.bounds.symbol_identities or len(identities) > 50_000:
            return False
        if len({item.issuer_id for item in identities}) > self.bounds.symbol_identities:
            return False
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT value FROM repository_metadata WHERE key='sec_ticker_map_revision' LIMIT 1"
            ).fetchone()
            prior_observed = connection.execute(
                "SELECT value FROM repository_metadata WHERE key='sec_ticker_map_last_observed' LIMIT 1"
            ).fetchone()
            if prior_observed is not None:
                incoming_observed = ticker_map.observed_at
                stored_observed = datetime.fromisoformat(prior_observed[0])
                if incoming_observed < stored_observed:
                    return False
                if incoming_observed == stored_observed and (prior is None or prior[0] != ticker_map.source_revision):
                    return False
            if prior is not None and prior[0] == ticker_map.source_revision:
                connection.execute(
                    "INSERT INTO repository_metadata(key,value) VALUES('sec_ticker_map_last_checked',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (ticker_map.observed_at.isoformat(),),
                )
                return True
            current_rows = connection.execute(
                "SELECT symbol,issuer_id,valid_from FROM symbol_aliases WHERE valid_to IS NULL"
            ).fetchall()
            current = {row["symbol"]: row for row in current_rows}
            incoming_symbols = {item.normalized_symbol for item in identities}
            projected_aliases = len(current_rows) + len(incoming_symbols - set(current))
            if projected_aliases > self.bounds.symbol_aliases:
                return False
            existing_issuers = {
                row[0] for row in connection.execute("SELECT issuer_id FROM symbol_identities")
            }
            new_issuers = {item.issuer_id for item in identities} - existing_issuers
            if len(existing_issuers) + len(new_issuers) > self.bounds.symbol_identities:
                return False
            for item in identities:
                connection.execute(
                    """INSERT INTO symbol_identities(issuer_id,canonical_symbol,issuer_name,updated_at,
                    cik,exchange,share_class,source_revision,verified) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(issuer_id) DO UPDATE SET canonical_symbol=excluded.canonical_symbol,
                    issuer_name=excluded.issuer_name,updated_at=excluded.updated_at,cik=excluded.cik,
                    exchange=excluded.exchange,share_class=excluded.share_class,
                    source_revision=excluded.source_revision,verified=excluded.verified""",
                    (item.issuer_id, item.canonical_ticker, item.issuer_name, item.observed_at.isoformat(),
                     item.cik, item.exchange, item.share_class, item.source_revision, int(item.verified)),
                )
                self._after_identity_mutation(item)
            observed = ticker_map.observed_at.isoformat()
            for symbol, row in current.items():
                incoming = next((item for item in identities if item.normalized_symbol == symbol), None)
                if incoming is None or incoming.issuer_id != row["issuer_id"]:
                    valid_from = row["valid_from"]
                    if valid_from < observed:
                        connection.execute(
                            "UPDATE symbol_aliases SET valid_to=? WHERE symbol=? AND valid_from=?",
                            (observed, symbol, valid_from),
                        )
                    else:
                        connection.execute(
                            "DELETE FROM symbol_aliases WHERE symbol=? AND valid_from=?",
                            (symbol, valid_from),
                        )
            for item in identities:
                old = current.get(item.normalized_symbol)
                if old is not None and old["issuer_id"] == item.issuer_id:
                    continue
                connection.execute(
                    "INSERT INTO symbol_aliases(symbol,issuer_id,valid_from,valid_to,source_revision) VALUES(?,?,?,?,?)",
                    (item.normalized_symbol, item.issuer_id, observed, None, item.source_revision),
                )
            for key, value in (
                ("sec_ticker_map_revision", ticker_map.source_revision),
                ("sec_ticker_map_last_observed", observed),
            ):
                connection.execute(
                    "INSERT INTO repository_metadata(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value),
                )
            return True

    def _after_identity_mutation(self, identity: Any) -> None:
        """Test seam; called inside the map transaction after a mutation."""
        return None

    def resolve_symbol_identity(self, symbol: str, as_of: datetime) -> Any:
        from .providers.sec_identity import SecIssuerResolution, SecResolutionStatus, normalize_sec_symbol
        normalized = normalize_sec_symbol(symbol)
        cutoff = _aware_iso(as_of)
        with self._operation_connection() as connection:
            rows = connection.execute(
                """SELECT a.*,i.* FROM symbol_aliases a JOIN symbol_identities i ON i.issuer_id=a.issuer_id
                WHERE a.symbol=? AND i.cik IS NOT NULL AND a.valid_from<=? AND (a.valid_to IS NULL OR ?<a.valid_to)
                ORDER BY a.valid_from DESC LIMIT 2""", (normalized, cutoff, cutoff),
            ).fetchall()
        if not rows:
            return SecIssuerResolution(symbol, normalized, SecResolutionStatus.UNRESOLVED, as_of=as_of, reason="NO_ALIAS")
        if len(rows) > 1:
            return SecIssuerResolution(symbol, normalized, SecResolutionStatus.AMBIGUOUS, as_of=as_of, reason="OVERLAPPING_ALIASES")
        row = rows[0]
        from .providers.sec_identity import SecIssuerIdentity
        identity = SecIssuerIdentity(
            normalized_symbol=normalized, cik=int(row["cik"]), issuer_id=row["issuer_id"],
            canonical_ticker=row["canonical_symbol"], issuer_name=row["issuer_name"], exchange=row["exchange"],
            share_class=row["share_class"], source_revision=row["source_revision"],
            observed_at=datetime.fromisoformat(row["updated_at"]), verified=bool(row["verified"]),
        )
        return SecIssuerResolution(symbol, normalized, SecResolutionStatus.RESOLVED, identity=identity, as_of=as_of)

    def current_symbol_identity(self, symbol: str) -> Any:
        from .providers.sec_identity import SecResolutionStatus
        return self.resolve_symbol_identity(symbol, datetime.now(UTC))

    def recover_current_identities(self, *, limit: int = 32_768) -> tuple[Any, ...]:
        if limit <= 0 or limit > min(self.bounds.symbol_identities, self.bounds.symbol_aliases):
            raise ValueError("identity recovery limit exceeds repository bound")
        from .providers.sec_identity import SecIssuerIdentity
        with self._operation_connection() as connection:
            rows = connection.execute(
                """SELECT a.symbol,i.* FROM symbol_aliases a JOIN symbol_identities i ON i.issuer_id=a.issuer_id
                WHERE a.valid_to IS NULL AND i.cik IS NOT NULL ORDER BY a.symbol LIMIT ?""", (limit,)
            ).fetchall()
        return tuple(SecIssuerIdentity(
            normalized_symbol=row["symbol"], cik=int(row["cik"]), issuer_id=row["issuer_id"],
            canonical_ticker=row["canonical_symbol"], issuer_name=row["issuer_name"], exchange=row["exchange"],
            share_class=row["share_class"], source_revision=row["source_revision"],
            observed_at=datetime.fromisoformat(row["updated_at"]), verified=bool(row["verified"]),
        ) for row in rows)

    def set_repository_metadata(self, key: str, value: str) -> None:
        safe_key = validate_safe_key(key)
        if safe_key == "schema_version":
            raise ValueError("schema_version is repository-owned")
        safe_value = validate_safe_text(value, field="repository metadata value", maximum=512)
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM repository_metadata WHERE key=? LIMIT 1", (safe_key,)
            ).fetchone()
            if not exists and _table_count(connection, "repository_metadata") >= self.bounds.repository_metadata:
                raise ValueError("repository metadata bound reached")
            connection.execute(
                "INSERT INTO repository_metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (safe_key, safe_value),
            )

    def append_evidence(self, events: Iterable[IntelligenceEvent]) -> IngestResult:
        inserted = deduplicated = rejected = 0
        reasons: Counter[str] = Counter()
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = _table_count(connection, "raw_events")
            for event in _bounded_batch(events):
                if not isinstance(event, IntelligenceEvent):
                    rejected += 1
                    reasons["INVALID_EVENT_CONTRACT"] += 1
                    continue
                _json(event.metadata)
                duplicate = connection.execute(
                    "SELECT 1 FROM raw_events WHERE event_id=? OR "
                    "(? IS NOT NULL AND source=? AND source_id=?) LIMIT 1",
                    (event.event_id, event.source_id, event.source, event.source_id),
                ).fetchone()
                if duplicate:
                    deduplicated += 1
                    continue
                if count >= self.bounds.raw_events:
                    rejected += 1
                    reasons["RAW_EVENT_BOUND_REACHED"] += 1
                    continue
                try:
                    connection.execute(
                        """INSERT INTO raw_events(
                        event_id,symbol,issuer_id,event_type,event_subtype,source,source_id,
                        published_at,observed_at,effective_at,headline,summary,source_reference,
                        verified,decay_class,supersedes_event_id,fact_schema_version,
                        source_parser_version,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        _event_row(event),
                    )
                except sqlite3.IntegrityError:
                    rejected += 1
                    reasons["REFERENTIAL_INTEGRITY"] += 1
                else:
                    inserted += 1
                    count += 1
        self._record_event_metrics(inserted, deduplicated, rejected)
        return IngestResult(inserted, deduplicated, rejected, tuple(sorted(reasons.items())))

    def upsert_derivations(self, derivations: Iterable[IntelligenceDerivation]) -> IngestResult:
        inserted = deduplicated = rejected = 0
        reasons: Counter[str] = Counter()
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = _table_count(connection, "event_derivations")
            for item in _bounded_batch(derivations):
                if not isinstance(item, IntelligenceDerivation):
                    rejected += 1
                    reasons["INVALID_DERIVATION_CONTRACT"] += 1
                    continue
                _json(item.metadata)
                if connection.execute(
                    "SELECT 1 FROM event_derivations WHERE derivation_id=? LIMIT 1",
                    (item.derivation_id,),
                ).fetchone():
                    deduplicated += 1
                    continue
                placeholders = ",".join("?" for _ in item.supporting_event_ids)
                facts = connection.execute(
                    f"SELECT event_id,published_at,observed_at FROM raw_events WHERE event_id IN ({placeholders})",
                    item.supporting_event_ids,
                ).fetchall()
                if len(facts) != len(item.supporting_event_ids) or any(
                    datetime.fromisoformat(row["published_at"]) > item.fact_cutoff
                    or datetime.fromisoformat(row["observed_at"]) > item.fact_cutoff
                    for row in facts
                ):
                    rejected += 1
                    reasons["SUPPORTING_FACT_UNAVAILABLE_AT_CUTOFF"] += 1
                    continue
                if count >= self.bounds.derivations:
                    rejected += 1
                    reasons["DERIVATION_BOUND_REACHED"] += 1
                    continue
                connection.execute(
                    """INSERT INTO event_derivations(
                    derivation_id,symbol,derivation_type,direction,significance,confidence,
                    algorithm_id,algorithm_version,generated_at,fact_cutoff,sample_count,
                    expires_at,stale,supporting_event_ids_json,derivation_schema_version,
                    metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    _derivation_row(item),
                )
                inserted += 1
                count += 1
        return IngestResult(inserted, deduplicated, rejected, tuple(sorted(reasons.items())))

    def upsert_symbol_identity(self, identity: SymbolIdentity) -> bool:
        if not isinstance(identity, SymbolIdentity):
            raise TypeError("identity must be SymbolIdentity")
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM symbol_identities WHERE issuer_id=? LIMIT 1", (identity.issuer_id,)
            ).fetchone()
            if not exists and _table_count(connection, "symbol_identities") >= self.bounds.symbol_identities:
                return False
            connection.execute(
                """INSERT INTO symbol_identities(issuer_id,canonical_symbol,issuer_name,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(issuer_id) DO UPDATE SET
                canonical_symbol=excluded.canonical_symbol,issuer_name=excluded.issuer_name,
                updated_at=excluded.updated_at""",
                (identity.issuer_id, identity.canonical_symbol, identity.issuer_name, identity.updated_at.isoformat()),
            )
            return True

    def upsert_symbol_alias(self, alias: SymbolAlias) -> bool:
        if not isinstance(alias, SymbolAlias):
            raise TypeError("alias must be SymbolAlias")
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM symbol_aliases WHERE symbol=? AND valid_from=? LIMIT 1",
                (alias.symbol, alias.valid_from.isoformat()),
            ).fetchone()
            if not exists and _table_count(connection, "symbol_aliases") >= self.bounds.symbol_aliases:
                return False
            connection.execute(
                """INSERT INTO symbol_aliases(symbol,issuer_id,valid_from,valid_to)
                VALUES(?,?,?,?) ON CONFLICT(symbol,valid_from) DO UPDATE SET
                issuer_id=excluded.issuer_id,valid_to=excluded.valid_to""",
                (alias.symbol, alias.issuer_id, alias.valid_from.isoformat(), _time(alias.valid_to)),
            )
            return True

    def store_source_state(self, state: SourceStateSnapshot) -> bool:
        if not isinstance(state, SourceStateSnapshot):
            raise TypeError("state must be SourceStateSnapshot")
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM source_states WHERE source=? LIMIT 1", (state.source,)
            ).fetchone()
            if not exists and _table_count(connection, "source_states") >= self.bounds.source_states:
                return False
            connection.execute(
                """INSERT INTO source_states(source,availability,observed_at,stale_after)
                VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET availability=excluded.availability,
                observed_at=excluded.observed_at,stale_after=excluded.stale_after""",
                (state.source, state.availability.value, state.observed_at.isoformat(), _time(state.stale_after)),
            )
            return True

    def get_source_state(self, source: str) -> SourceStateSnapshot | None:
        """Read the latest bounded source state without mutating the repository.

        Source states are current-state records, not a history.  Consequently
        this API intentionally has no historical cutoff argument.
        """
        normalized = validate_safe_text(source.upper(), field="source", maximum=64)
        with self._operation_connection() as connection:
            row = connection.execute(
                "SELECT source,availability,observed_at,stale_after FROM source_states "
                "WHERE source=? LIMIT 1",
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return SourceStateSnapshot(
            source=row["source"],
            availability=SourceAvailability(row["availability"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            stale_after=(
                None if row["stale_after"] is None
                else datetime.fromisoformat(row["stale_after"])
            ),
        )

    def store_active_event_state(
        self, *, event_id: str, symbol: str, state: str,
        updated_at: datetime, expires_at: datetime | None = None,
    ) -> bool:
        values = (
            validate_safe_text(event_id, field="event_id", maximum=256),
            validate_safe_text(symbol.upper(), field="symbol", maximum=32),
            validate_safe_text(state, field="active event state", maximum=64),
            _aware_iso(updated_at), _time(expires_at),
        )
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM active_event_states WHERE event_id=? LIMIT 1", (values[0],)
            ).fetchone()
            if not exists and _table_count(connection, "active_event_states") >= self.bounds.active_event_states:
                return False
            connection.execute(
                """INSERT INTO active_event_states(event_id,symbol,state,updated_at,expires_at)
                VALUES(?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET state=excluded.state,
                updated_at=excluded.updated_at,expires_at=excluded.expires_at""", values,
            )
            return True

    def store_episode_summary(
        self, *, episode_id: str, symbol: str, episode_type: str,
        started_at: datetime, ended_at: datetime | None, summary: Mapping[str, Any],
    ) -> bool:
        safe_summary = _json(safe_json_value(summary))
        values = (
            validate_safe_text(episode_id, field="episode_id", maximum=256),
            validate_safe_text(symbol.upper(), field="symbol", maximum=32),
            validate_safe_text(episode_type, field="episode_type", maximum=128),
            _aware_iso(started_at), _time(ended_at), safe_summary,
        )
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM symbol_episode_summaries WHERE episode_id=? LIMIT 1", (values[0],)
            ).fetchone()
            if not exists and _table_count(connection, "symbol_episode_summaries") >= self.bounds.episode_summaries:
                return False
            connection.execute(
                """INSERT INTO symbol_episode_summaries(
                episode_id,symbol,episode_type,started_at,ended_at,summary_json)
                VALUES(?,?,?,?,?,?) ON CONFLICT(episode_id) DO UPDATE SET
                ended_at=excluded.ended_at,summary_json=excluded.summary_json""", values,
            )
            return True

    def store_snapshot(self, snapshot: SymbolIntelligenceSnapshot) -> bool:
        if not isinstance(snapshot, SymbolIntelligenceSnapshot):
            raise TypeError("snapshot must be SymbolIntelligenceSnapshot")
        payload = _snapshot_json(snapshot)
        with self._operation_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT 1 FROM symbol_snapshots WHERE symbol=? AND as_of=? AND generated_at=? AND snapshot_version=? LIMIT 1",
                (snapshot.symbol, snapshot.as_of.isoformat(), snapshot.generated_at.isoformat(), snapshot.snapshot_version),
            ).fetchone()
            if duplicate:
                return False
            if _table_count(connection, "symbol_snapshots") >= self.bounds.snapshots:
                return False
            current = connection.execute(
                "SELECT as_of,generated_at FROM symbol_snapshots WHERE symbol=? AND is_current=1 LIMIT 1",
                (snapshot.symbol,),
            ).fetchone()
            is_current = current is None or (
                snapshot.as_of.isoformat(), snapshot.generated_at.isoformat()
            ) >= (current["as_of"], current["generated_at"])
            if is_current:
                connection.execute("UPDATE symbol_snapshots SET is_current=0 WHERE symbol=? AND is_current=1", (snapshot.symbol,))
            connection.execute(
                """INSERT INTO symbol_snapshots(
                symbol,as_of,generated_at,fact_cutoff,snapshot_version,attention_state,
                attention_rank,priority_score,is_current,payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (snapshot.symbol, snapshot.as_of.isoformat(), snapshot.generated_at.isoformat(),
                 _time(snapshot.fact_cutoff), snapshot.snapshot_version, snapshot.attention_state.value,
                 ATTENTION_RANK[snapshot.attention_state], snapshot.priority_score, int(is_current), payload),
            )
            return True

    def snapshot_at(self, symbol: str, as_of: datetime) -> SymbolIntelligenceSnapshot | None:
        normalized = validate_safe_text(symbol.upper(), field="symbol", maximum=32)
        cutoff = _aware_iso(as_of)
        with self._operation_connection() as connection:
            row = connection.execute(
                """SELECT payload_json FROM symbol_snapshots
                WHERE symbol=? AND as_of<=? AND generated_at<=?
                  AND (fact_cutoff IS NULL OR fact_cutoff<=?)
                ORDER BY as_of DESC,generated_at DESC LIMIT 1""",
                (normalized, cutoff, cutoff, cutoff),
            ).fetchone()
        return None if row is None else _snapshot_from_json(row["payload_json"])

    def recent_events(
        self, symbol: str, *, limit: int,
        since: datetime | None = None, until: datetime | None = None,
    ) -> tuple[IntelligenceEvent, ...]:
        _query_limit(limit)
        normalized = validate_safe_text(symbol.upper(), field="symbol", maximum=32)
        clauses = ["symbol=?"]
        values: list[Any] = [normalized]
        if since is not None:
            clauses.append("observed_at>=?")
            values.append(_aware_iso(since))
        if until is not None:
            cutoff = _aware_iso(until)
            clauses.extend(("observed_at<=?", "published_at<=?"))
            values.extend((cutoff, cutoff))
        values.append(limit)
        with self._operation_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM raw_events WHERE " + " AND ".join(clauses)
                + " ORDER BY observed_at DESC,event_id DESC LIMIT ?", values,
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def recent_sec_events_by_issuer(
        self,
        issuer_id: str,
        *,
        limit: int,
        fact_cutoff: datetime,
    ) -> tuple[IntelligenceEvent, ...]:
        """Return bounded, point-in-time SEC facts for one issuer.

        Both publication and observation timestamps are constrained so a
        caller cannot observe facts that were not available at ``fact_cutoff``.
        The query is issuer-centric and deliberately does not apply catalyst
        freshness or semantic derivation rules.
        """
        _query_limit(limit)
        normalized_issuer = validate_safe_text(issuer_id, field="issuer_id", maximum=128)
        if fact_cutoff.tzinfo is None or fact_cutoff.utcoffset() is None:
            cutoff = _aware_iso(fact_cutoff)
        else:
            cutoff = fact_cutoff.astimezone(UTC).isoformat()
        with self._operation_connection() as connection:
            rows = connection.execute(
                """SELECT * FROM raw_events
                WHERE issuer_id=? AND source=? AND event_type=?
                  AND published_at<=? AND observed_at<=?
                ORDER BY published_at DESC,observed_at DESC,source_id DESC,event_id DESC
                LIMIT ?""",
                (normalized_issuer, "SEC_EDGAR", EventType.SEC_FILING.value,
                 cutoff, cutoff, limit),
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def recover_hot_state(
        self, *, limit: int = DEFAULT_SNAPSHOT_RECOVERY_MAX,
    ) -> tuple[SymbolIntelligenceSnapshot, ...]:
        if limit <= 0 or limit > DEFAULT_SNAPSHOT_RECOVERY_MAX or limit > DEFAULT_STARTUP_RECOVERY_MAX:
            raise ValueError("snapshot recovery limit exceeds the startup bound")
        started = perf_counter()
        with self._operation_connection() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM symbol_snapshots WHERE is_current=1
                ORDER BY attention_rank DESC,priority_score DESC,as_of DESC,symbol
                LIMIT ?""", (limit,),
            ).fetchall()
        snapshots = tuple(_snapshot_from_json(row["payload_json"]) for row in rows)
        elapsed = (perf_counter() - started) * 1_000.0
        with self._metrics_lock:
            self._recovery_rows = len(snapshots)
            self._recovery_milliseconds = elapsed
        return snapshots

    def metrics(self) -> RepositoryMetrics:
        with self._operation_connection() as connection:
            counts = tuple((table, _table_count(connection, table)) for table in _COUNTED_TABLES)
        with self._metrics_lock:
            return RepositoryMetrics(
                counts,
                self.path.stat().st_size if self.path.exists() else 0,
                _file_size(Path(str(self.path) + "-wal")),
                self._events_inserted,
                self._events_deduplicated,
                self._events_rejected,
                self._recovery_rows,
                self._recovery_milliseconds,
                self._sqlite_busy_or_locked,
            )

    @contextmanager
    def _operation_connection(self):
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            with connection:
                yield connection
        except sqlite3.OperationalError as exc:
            if "busy" in str(exc).casefold() or "locked" in str(exc).casefold():
                with self._metrics_lock:
                    self._sqlite_busy_or_locked += 1
            raise
        finally:
            if connection is not None:
                connection.close()

    def _record_event_metrics(self, inserted: int, deduplicated: int, rejected: int) -> None:
        with self._metrics_lock:
            self._events_inserted += inserted
            self._events_deduplicated += deduplicated
            self._events_rejected += rejected


def _query_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 or limit > MAX_QUERY_LIMIT:
        raise ValueError(f"query limit must be in 1..{MAX_QUERY_LIMIT}")


def _bounded_batch(values: Iterable[Any]) -> tuple[Any, ...]:
    iterator = iter(values)
    batch = tuple(islice(iterator, MAX_WRITE_BATCH + 1))
    if len(batch) > MAX_WRITE_BATCH:
        raise ValueError(f"write batch exceeds {MAX_WRITE_BATCH} items")
    return batch


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    if table not in _COUNTED_TABLES:
        raise ValueError("table is not count-allowlisted")
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _time(value: datetime | None) -> str | None:
    return None if value is None else _aware_iso(value)


def _aware_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat()


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _json(value: Any) -> str:
    safe = safe_json_value(_thaw(value))
    return json.dumps(_thaw(safe), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _event_row(item: IntelligenceEvent) -> tuple[Any, ...]:
    return (
        item.event_id, item.symbol, item.issuer_id, item.event_type.value,
        item.event_subtype, item.source, item.source_id, item.published_at.isoformat(),
        item.observed_at.isoformat(), _time(item.effective_at), item.headline,
        item.summary, item.source_reference, int(item.verified), item.decay_class.value,
        item.supersedes_event_id, item.fact_schema_version, item.source_parser_version,
        _json(item.metadata),
    )


def _event_from_row(row: sqlite3.Row) -> IntelligenceEvent:
    return IntelligenceEvent(
        event_id=row["event_id"], symbol=row["symbol"], issuer_id=row["issuer_id"],
        event_type=EventType(row["event_type"]), event_subtype=row["event_subtype"],
        source=row["source"], source_id=row["source_id"],
        published_at=datetime.fromisoformat(row["published_at"]),
        observed_at=datetime.fromisoformat(row["observed_at"]),
        effective_at=None if row["effective_at"] is None else datetime.fromisoformat(row["effective_at"]),
        headline=row["headline"], summary=row["summary"],
        source_reference=row["source_reference"], verified=bool(row["verified"]),
        decay_class=DecayClass(row["decay_class"]),
        supersedes_event_id=row["supersedes_event_id"],
        fact_schema_version=int(row["fact_schema_version"]),
        source_parser_version=row["source_parser_version"],
        metadata=json.loads(row["metadata_json"]),
    )


def _derivation_row(item: IntelligenceDerivation) -> tuple[Any, ...]:
    return (
        item.derivation_id, item.symbol, item.derivation_type, item.direction.value,
        item.significance, str(item.confidence), item.algorithm_id,
        item.algorithm_version, item.generated_at.isoformat(), item.fact_cutoff.isoformat(),
        item.sample_count, _time(item.expires_at), int(item.stale),
        _json(item.supporting_event_ids), item.derivation_schema_version, _json(item.metadata),
    )


def _snapshot_json(item: SymbolIntelligenceSnapshot) -> str:
    value = {
        "symbol": item.symbol, "issuer_id": item.issuer_id, "issuer_name": item.issuer_name,
        "as_of": item.as_of.isoformat(), "generated_at": item.generated_at.isoformat(),
        "snapshot_version": item.snapshot_version, "attention_state": item.attention_state.value,
        "priority_score": item.priority_score, "priority_reasons": item.priority_reasons,
        "active_catalysts": tuple({
            "event_id": value.event_id, "event_type": value.event_type.value,
            "event_subtype": value.event_subtype, "published_at": value.published_at.isoformat(),
            "direction": value.direction.value, "significance": value.significance,
            "confidence": None if value.confidence is None else str(value.confidence),
        } for value in item.active_catalysts),
        "freshest_material_event_id": item.freshest_material_event_id,
        "catalyst_age_bucket": item.catalyst_age_bucket,
        "catalyst_direction": item.catalyst_direction.value,
        "catalyst_significance": item.catalyst_significance,
        "catalyst_confidence": None if item.catalyst_confidence is None else str(item.catalyst_confidence),
        "offering_dilution_risk": item.offering_dilution_risk,
        "recent_reverse_split_event_id": item.recent_reverse_split_event_id,
        "recent_material_filing_event_id": item.recent_material_filing_event_id,
        "recent_runner_summary": item.recent_runner_summary,
        "recent_halt_summary": item.recent_halt_summary,
        "recent_opportunity_summary": item.recent_opportunity_summary,
        "source_states": tuple({
            "source": value.source, "availability": value.availability.value,
            "observed_at": value.observed_at.isoformat(),
            "stale_after": _time(value.stale_after),
        } for value in item.source_states),
        "fact_cutoff": _time(item.fact_cutoff),
        "derivation_versions": item.derivation_versions,
        "missing_fields": item.missing_fields, "unknown": item.unknown, "stale": item.stale,
    }
    return _json(value)


def _snapshot_from_json(payload: str) -> SymbolIntelligenceSnapshot:
    raw = json.loads(payload)
    return SymbolIntelligenceSnapshot(
        symbol=raw["symbol"], issuer_id=raw.get("issuer_id"), issuer_name=raw.get("issuer_name"),
        as_of=datetime.fromisoformat(raw["as_of"]), generated_at=datetime.fromisoformat(raw["generated_at"]),
        snapshot_version=int(raw["snapshot_version"]), attention_state=AttentionState(raw["attention_state"]),
        priority_score=int(raw["priority_score"]), priority_reasons=tuple(raw["priority_reasons"]),
        active_catalysts=tuple(CatalystSummary(
            event_id=value["event_id"], event_type=EventType(value["event_type"]),
            event_subtype=value["event_subtype"], published_at=datetime.fromisoformat(value["published_at"]),
            direction=Direction(value["direction"]), significance=value["significance"],
            confidence=None if value["confidence"] is None else Decimal(value["confidence"]),
        ) for value in raw["active_catalysts"]),
        freshest_material_event_id=raw.get("freshest_material_event_id"),
        catalyst_age_bucket=raw.get("catalyst_age_bucket"),
        catalyst_direction=Direction(raw["catalyst_direction"]),
        catalyst_significance=raw.get("catalyst_significance"),
        catalyst_confidence=None if raw.get("catalyst_confidence") is None else Decimal(raw["catalyst_confidence"]),
        offering_dilution_risk=raw.get("offering_dilution_risk"),
        recent_reverse_split_event_id=raw.get("recent_reverse_split_event_id"),
        recent_material_filing_event_id=raw.get("recent_material_filing_event_id"),
        recent_runner_summary=raw.get("recent_runner_summary"),
        recent_halt_summary=raw.get("recent_halt_summary"),
        recent_opportunity_summary=raw.get("recent_opportunity_summary"),
        source_states=tuple(SourceStateSnapshot(
            source=value["source"], availability=SourceAvailability(value["availability"]),
            observed_at=datetime.fromisoformat(value["observed_at"]),
            stale_after=None if value["stale_after"] is None else datetime.fromisoformat(value["stale_after"]),
        ) for value in raw["source_states"]),
        fact_cutoff=None if raw.get("fact_cutoff") is None else datetime.fromisoformat(raw["fact_cutoff"]),
        derivation_versions=tuple(raw["derivation_versions"]), missing_fields=tuple(raw["missing_fields"]),
        unknown=bool(raw["unknown"]), stale=bool(raw["stale"]),
    )


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Add SEC identity provenance without discarding Phase-1 rows."""
    for statement in (
        "ALTER TABLE symbol_identities ADD COLUMN cik INTEGER",
        "ALTER TABLE symbol_identities ADD COLUMN exchange TEXT",
        "ALTER TABLE symbol_identities ADD COLUMN share_class TEXT",
        "ALTER TABLE symbol_identities ADD COLUMN source_revision TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE symbol_identities ADD COLUMN verified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE symbol_aliases ADD COLUMN source_revision TEXT NOT NULL DEFAULT ''",
    ):
        try:
            connection.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).casefold():
                raise RepositorySchemaError("identity schema migration failed") from exc
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_symbol_alias_current ON symbol_aliases(symbol,valid_from DESC)"
    )
    try:
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_symbol_alias_current "
            "ON symbol_aliases(symbol) WHERE valid_to IS NULL"
        )
    except sqlite3.IntegrityError as exc:
        raise RepositorySchemaError("existing identity aliases overlap") from exc


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Support bounded issuer/source/time lookups for SEC facts."""
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_raw_event_issuer_source_published "
        "ON raw_events(issuer_id,source,event_type,published_at DESC,observed_at DESC,event_id DESC)"
    )


_SCHEMA = """
CREATE TABLE repository_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE source_states(
    source TEXT PRIMARY KEY,availability TEXT NOT NULL,observed_at TEXT NOT NULL,stale_after TEXT);
CREATE TABLE symbol_identities(
    issuer_id TEXT PRIMARY KEY,canonical_symbol TEXT NOT NULL,issuer_name TEXT,updated_at TEXT NOT NULL,
    cik INTEGER,exchange TEXT,share_class TEXT,source_revision TEXT NOT NULL DEFAULT '',verified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE symbol_aliases(
    symbol TEXT NOT NULL,issuer_id TEXT NOT NULL,valid_from TEXT NOT NULL,valid_to TEXT,source_revision TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(symbol,valid_from),FOREIGN KEY(issuer_id) REFERENCES symbol_identities(issuer_id));
CREATE INDEX ix_symbol_alias_current ON symbol_aliases(symbol,valid_from DESC);
CREATE UNIQUE INDEX ux_symbol_alias_current ON symbol_aliases(symbol) WHERE valid_to IS NULL;
CREATE TABLE raw_events(
    event_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,issuer_id TEXT,event_type TEXT NOT NULL,
    event_subtype TEXT NOT NULL,source TEXT NOT NULL,source_id TEXT,published_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,effective_at TEXT,headline TEXT,summary TEXT,source_reference TEXT,
    verified INTEGER NOT NULL,decay_class TEXT NOT NULL,supersedes_event_id TEXT,
    fact_schema_version INTEGER NOT NULL,source_parser_version TEXT NOT NULL,metadata_json TEXT NOT NULL,
    FOREIGN KEY(supersedes_event_id) REFERENCES raw_events(event_id));
CREATE UNIQUE INDEX ux_raw_event_source_identity ON raw_events(source,source_id) WHERE source_id IS NOT NULL;
CREATE INDEX ix_raw_event_symbol_observed ON raw_events(symbol,observed_at DESC,event_id DESC);
CREATE INDEX ix_raw_event_type_observed ON raw_events(event_type,observed_at DESC,event_id DESC);
CREATE INDEX ix_raw_event_issuer_source_published ON raw_events(issuer_id,source,event_type,published_at DESC,observed_at DESC,event_id DESC);
CREATE TABLE event_derivations(
    derivation_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,derivation_type TEXT NOT NULL,direction TEXT NOT NULL,
    significance INTEGER NOT NULL,confidence TEXT NOT NULL,algorithm_id TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,generated_at TEXT NOT NULL,fact_cutoff TEXT NOT NULL,
    sample_count INTEGER,expires_at TEXT,stale INTEGER NOT NULL,supporting_event_ids_json TEXT NOT NULL,
    derivation_schema_version INTEGER NOT NULL,metadata_json TEXT NOT NULL);
CREATE INDEX ix_derivation_symbol_cutoff ON event_derivations(symbol,fact_cutoff DESC,derivation_id);
CREATE TABLE active_event_states(
    event_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,state TEXT NOT NULL,updated_at TEXT NOT NULL,expires_at TEXT,
    FOREIGN KEY(event_id) REFERENCES raw_events(event_id) ON DELETE CASCADE);
CREATE INDEX ix_active_state_symbol_updated ON active_event_states(symbol,updated_at DESC,event_id);
CREATE TABLE symbol_episode_summaries(
    episode_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,episode_type TEXT NOT NULL,
    started_at TEXT NOT NULL,ended_at TEXT,summary_json TEXT NOT NULL);
CREATE INDEX ix_episode_symbol_started ON symbol_episode_summaries(symbol,started_at DESC,episode_id);
CREATE TABLE symbol_snapshots(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,as_of TEXT NOT NULL,
    generated_at TEXT NOT NULL,fact_cutoff TEXT,snapshot_version INTEGER NOT NULL,
    attention_state TEXT NOT NULL,attention_rank INTEGER NOT NULL,priority_score INTEGER NOT NULL,
    is_current INTEGER NOT NULL,payload_json TEXT NOT NULL,
    UNIQUE(symbol,as_of,generated_at,snapshot_version));
CREATE UNIQUE INDEX ux_snapshot_current_symbol ON symbol_snapshots(symbol) WHERE is_current=1;
CREATE INDEX ix_snapshot_point_in_time ON symbol_snapshots(symbol,as_of DESC,generated_at DESC,fact_cutoff);
CREATE INDEX ix_snapshot_recovery ON symbol_snapshots(is_current,attention_rank DESC,priority_score DESC,as_of DESC,symbol);
"""


__all__ = [
    "DEFAULT_SNAPSHOT_RECOVERY_MAX", "DEFAULT_STARTUP_RECOVERY_MAX",
    "MAX_QUERY_LIMIT", "MAX_WRITE_BATCH", "REPOSITORY_SCHEMA_VERSION", "RepositoryBounds",
    "RepositoryMetrics", "RepositorySchemaError", "SymbolIntelligenceRepository",
]

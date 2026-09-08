"""Bounded external JSONL collection for real crypto catalyst research.

This module is a downstream research sink.  It accepts immutable Phase C, Phase G,
and Phase D records but never owns providers, strategy selection, or execution.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from typing import Callable, Iterable, Mapping

from app.research_core import semantic_digest

from .catalyst_outcomes import (
    build_crypto_catalyst_outcome_observation,
)
from .catalyst_snapshot import CryptoCatalystDecisionSnapshot
from .outcomes import (
    SUPPORTED_HORIZONS_SECONDS,
    CryptoOutcomeDecision,
    CryptoResearchOutcome,
)


SCHEMA_VERSION = "crypto-catalyst-collection-v1"
DECISION_RECORD = "DECISION"
OUTCOME_RECORD = "OUTCOME_OBSERVATION"
RUN_METADATA_RECORD = "RUN_METADATA"
RUN_END_RECORD = "RUN_END"
DEFAULT_QUEUE_CAPACITY = 256
DEFAULT_FLUSH_SECONDS = 1.0
DEFAULT_MAX_DEDUP_IDENTITIES = 16384
DEFAULT_MAX_PENDING_DECISIONS = 8192


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collection timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CryptoCatalystCollectionConfig:
    enabled: bool = False
    path: str | Path | None = None
    queue_capacity: int = DEFAULT_QUEUE_CAPACITY
    flush_seconds: float = DEFAULT_FLUSH_SECONDS
    maximum_dedup_identities: int = DEFAULT_MAX_DEDUP_IDENTITIES
    maximum_pending_decisions: int = DEFAULT_MAX_PENDING_DECISIONS
    source_version: str | None = None

    def __post_init__(self) -> None:
        if self.queue_capacity <= 0 or self.maximum_dedup_identities <= 0 or self.maximum_pending_decisions <= 0:
            raise ValueError("collection bounds must be positive")
        if self.flush_seconds <= 0:
            raise ValueError("flush_seconds must be positive")
        if self.enabled and self.path is None:
            raise ValueError("an external collection path is required when enabled")
        if self.path is not None and not str(self.path).strip():
            raise ValueError("collection path cannot be blank")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "CryptoCatalystCollectionConfig":
        values = os.environ if environ is None else environ
        enabled = str(values.get("CRYPTO_CATALYST_COLLECTION_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
        path = values.get("CRYPTO_CATALYST_COLLECTION_PATH")
        queue_capacity = int(values.get("CRYPTO_CATALYST_COLLECTION_QUEUE_CAPACITY", DEFAULT_QUEUE_CAPACITY))
        flush_seconds = float(values.get("CRYPTO_CATALYST_COLLECTION_FLUSH_SECONDS", DEFAULT_FLUSH_SECONDS))
        return cls(enabled, path, queue_capacity, flush_seconds,
                   int(values.get("CRYPTO_CATALYST_COLLECTION_MAX_DEDUP", DEFAULT_MAX_DEDUP_IDENTITIES)),
                   int(values.get("CRYPTO_CATALYST_COLLECTION_MAX_PENDING", DEFAULT_MAX_PENDING_DECISIONS)),
                   values.get("ATLAS_COMMIT_SHA"))


@dataclass(frozen=True, slots=True)
class CryptoCatalystCollectionMetrics:
    collection_enabled: bool
    run_id: str
    decisions_seen: int
    decisions_accepted: int
    decision_duplicates: int
    snapshots_captured: int
    outcomes_seen: int
    observations_built: int
    records_enqueued: int
    records_written: int
    queue_depth: int
    queue_high_water_mark: int
    records_dropped: int
    write_failures: int
    serialization_failures: int
    last_write_age_seconds: int | None
    shutdown_unflushed: int
    pending_decisions: int
    research_only: bool = True


@dataclass(frozen=True, slots=True)
class _FrozenDecision:
    decision: CryptoOutcomeDecision
    snapshot: CryptoCatalystDecisionSnapshot
    horizons: frozenset[int] = frozenset()


class CryptoCatalystCollectionSink:
    """Nonblocking, bounded, append-only G3A research sink."""

    def __init__(
        self,
        config: CryptoCatalystCollectionConfig | None = None,
        *,
        clock: Callable[[], datetime] = _now,
        process_identity: str = "process",
        repository_root: str | Path | None = None,
    ) -> None:
        self.config = config or CryptoCatalystCollectionConfig()
        self._clock = clock
        self._lock = RLock()
        self._stop = Event()
        self._queue: Queue[dict[str, object]] = Queue(maxsize=self.config.queue_capacity)
        self._worker: Thread | None = None
        self._accepting = False
        self._handle = None
        self._pending: OrderedDict[str, _FrozenDecision] = OrderedDict()
        self._dedup: OrderedDict[str, None] = OrderedDict()
        self._run_id = semantic_digest("g3a-run", SCHEMA_VERSION, _utc(clock()).isoformat(), process_identity)
        self._started_at = _utc(clock())
        self._decisions_seen = self._decisions_accepted = self._decision_duplicates = 0
        self._snapshots_captured = self._outcomes_seen = self._observations_built = 0
        self._records_enqueued = self._records_written = self._queue_high_water = 0
        self._records_dropped = self._write_failures = self._serialization_failures = 0
        self._last_write_at: datetime | None = None
        self._shutdown_unflushed = 0
        self._repository_root = Path(repository_root).resolve() if repository_root else None
        self._validate_path()

    def _validate_path(self) -> None:
        if not self.config.enabled:
            return
        path = Path(self.config.path).expanduser().resolve()  # type: ignore[arg-type]
        if path.suffix.casefold() not in {".jsonl", ".ndjson"}:
            raise ValueError("collection path must be JSONL/NDJSON")
        if path.suffix.casefold() in {".sqlite", ".sqlite3"}:
            raise ValueError("authoritative database paths are forbidden")
        if self._repository_root is not None:
            try:
                path.relative_to(self._repository_root)
            except ValueError:
                pass
            else:
                raise ValueError("collection path must be external to the repository")

    @property
    def run_id(self) -> str:
        return self._run_id

    def start(self) -> bool:
        if not self.config.enabled:
            return False
        with self._lock:
            if self._worker is not None:
                return False
            self._accepting = True
            self._worker = Thread(target=self._run_worker, name="atlas-crypto-catalyst-collector", daemon=True)
            self._worker.start()
        self._enqueue(self._run_record(RUN_METADATA_RECORD, {
            "started_at": self._started_at.isoformat(),
            "source_version": self.config.source_version,
            "queue_capacity": self.config.queue_capacity,
            "flush_seconds": self.config.flush_seconds,
        }))
        return True

    def _run_record(self, record_type: str, payload: Mapping[str, object]) -> dict[str, object]:
        return {"schema_version": SCHEMA_VERSION, "record_type": record_type, "run_id": self._run_id, **dict(payload),
                "research_only": True, "production_promoted": False, "selection_authorized": False, "execution_authorized": False}

    def _enqueue(self, record: dict[str, object]) -> bool:
        try:
            json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError, OverflowError):
            with self._lock:
                self._serialization_failures += 1
            return False
        try:
            self._queue.put_nowait(record)
        except Full:
            with self._lock:
                self._records_dropped += 1
            return False
        with self._lock:
            self._records_enqueued += 1
            self._queue_high_water = max(self._queue_high_water, self._queue.qsize())
        return True

    def capture_decision(self, decision: CryptoOutcomeDecision, snapshot: CryptoCatalystDecisionSnapshot) -> bool:
        """Freeze exactly one Phase G snapshot for a decision cutoff."""
        with self._lock:
            self._decisions_seen += 1
            if not self._accepting:
                return False
            key = decision.identity.deterministic_id
            if key in self._pending or key in self._dedup:
                self._decision_duplicates += 1
                return False
            if len(self._pending) >= self.config.maximum_pending_decisions:
                self._records_dropped += 1
                return False
            if decision.decision_cutoff != snapshot.decision_cutoff or decision.canonical_symbol != snapshot.canonical_pair:
                self._serialization_failures += 1
                return False
            self._pending[key] = _FrozenDecision(decision, snapshot)
            self._dedup[key] = None
            self._dedup.move_to_end(key)
            while len(self._dedup) > self.config.maximum_dedup_identities:
                self._dedup.popitem(last=False)
        record = self._run_record(DECISION_RECORD, {
            "decision_identity": key,
            "decision": decision.to_dict(),
            "snapshot_identity": snapshot.snapshot_identity,
            "snapshot": snapshot.to_dict(),
        })
        if not self._enqueue(record):
            with self._lock:
                self._pending.pop(key, None)
                self._dedup.pop(key, None)
            return False
        with self._lock:
            self._decisions_accepted += 1
            self._snapshots_captured += 1
        return True

    def record_outcome(self, decision: CryptoOutcomeDecision, outcome: CryptoResearchOutcome) -> bool:
        """Build G2 from the retained decision-time snapshot, never latest evidence."""
        with self._lock:
            self._outcomes_seen += 1
            frozen = self._pending.get(decision.identity.deterministic_id)
        if frozen is None:
            return False
        if outcome.decision.identity.deterministic_id != decision.identity.deterministic_id:
            return False
        try:
            observation = build_crypto_catalyst_outcome_observation(frozen.decision, frozen.snapshot, outcome)
            payload = self._run_record(OUTCOME_RECORD, {
                "observation_identity": observation.observation_identity,
                "observation": observation.to_dict(),
            })
        except (TypeError, ValueError, OverflowError):
            with self._lock:
                self._serialization_failures += 1
            return False
        with self._lock:
            if outcome.horizon_seconds in frozen.horizons:
                self._decision_duplicates += 1
                return False
        if not self._enqueue(payload):
            return False
        with self._lock:
            updated = _FrozenDecision(frozen.decision, frozen.snapshot, frozen.horizons | {outcome.horizon_seconds})
            self._pending[decision.identity.deterministic_id] = updated
            if len(updated.horizons) >= len(SUPPORTED_HORIZONS_SECONDS):
                self._pending.pop(decision.identity.deterministic_id, None)
            self._observations_built += 1
        return True

    def flush(self, timeout_seconds: float = 5.0) -> bool:
        deadline = _utc(self._clock()).timestamp() + max(0.0, timeout_seconds)
        while self._queue.unfinished_tasks and _utc(self._clock()).timestamp() < deadline:
            self._stop.wait(0.01)
        return self._queue.unfinished_tasks == 0

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            self._accepting = False
            self._stop.set()
        worker = self._worker
        if worker is not None:
            worker.join(max(0.0, timeout_seconds))
        with self._lock:
            self._shutdown_unflushed = self._queue.qsize()
        if self.config.enabled and (worker is None or not worker.is_alive()):
            try:
                self._write(self._run_record(RUN_END_RECORD, {
                    "ended_at": _utc(self._clock()).isoformat(),
                    "normal_shutdown": True,
                    "records_written": self._records_written,
                    "records_dropped": self._records_dropped,
                    "shutdown_unflushed": self._shutdown_unflushed,
                }))
            except Exception:
                with self._lock:
                    self._write_failures += 1
        if self._handle is not None:
            try:
                self._handle.flush()
                self._handle.close()
            except Exception:
                with self._lock:
                    self._write_failures += 1
            self._handle = None
        return worker is None or not worker.is_alive()

    def _run_worker(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                record = self._queue.get(timeout=min(self.config.flush_seconds, 0.1))
            except Empty:
                continue
            try:
                self._write(record)
            except Exception:
                with self._lock:
                    self._write_failures += 1
            finally:
                self._queue.task_done()

    def _write(self, record: Mapping[str, object]) -> None:
        path = Path(self.config.path).expanduser()  # type: ignore[arg-type]
        if self._handle is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("a", encoding="utf-8", newline="\n")
        self._handle.write(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        self._handle.flush()
        with self._lock:
            self._records_written += 1
            self._last_write_at = _utc(self._clock())

    def metrics(self) -> CryptoCatalystCollectionMetrics:
        with self._lock:
            age = None if self._last_write_at is None else max(0, int((_utc(self._clock()) - self._last_write_at).total_seconds()))
            return CryptoCatalystCollectionMetrics(
                self.config.enabled, self._run_id, self._decisions_seen, self._decisions_accepted,
                self._decision_duplicates, self._snapshots_captured, self._outcomes_seen,
                self._observations_built, self._records_enqueued, self._records_written,
                self._queue.qsize(), self._queue_high_water, self._records_dropped,
                self._write_failures, self._serialization_failures, age, self._shutdown_unflushed,
                len(self._pending), True,
            )


def replay_crypto_collection(path: str | Path) -> tuple[dict[str, object], ...]:
    """Read G3A JSONL without providers, Webull, broker, GUI, or SQLite."""
    output: list[dict[str, object]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("invalid G3A record")
            output.append(value)
    return tuple(output)


__all__ = [
    "SCHEMA_VERSION", "DECISION_RECORD", "OUTCOME_RECORD", "RUN_METADATA_RECORD", "RUN_END_RECORD",
    "DEFAULT_QUEUE_CAPACITY", "CryptoCatalystCollectionConfig", "CryptoCatalystCollectionMetrics",
    "CryptoCatalystCollectionSink", "replay_crypto_collection",
]

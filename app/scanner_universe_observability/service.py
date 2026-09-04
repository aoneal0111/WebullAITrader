"""Bounded, failure-isolated scanner-universe admission observer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from zoneinfo import ZoneInfo

from .models import (
    UniverseAdmissionEvent,
    UniverseAdmissionMetrics,
    UniverseAdmissionOutcome,
    UniverseAdmissionStage,
)
from .store import UniverseAdmissionJsonlStore


_NEW_YORK = ZoneInfo("America/New_York")
_LOGGER = logging.getLogger("atlas.research.scanner_universe")


@dataclass(frozen=True, slots=True)
class _Work:
    event: UniverseAdmissionEvent


class ScannerUniverseAdmissionObserver:
    """One-way research sink; callers never consume an observation result."""

    def __init__(
        self,
        *,
        enabled: bool,
        path: str | Path,
        capacity: int = 4096,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        store_factory: Callable[[Path], object] = UniverseAdmissionJsonlStore,
    ) -> None:
        self.enabled = bool(enabled)
        self.path = Path(path)
        self.capacity = capacity
        self._clock = clock
        self._lock = RLock()
        self._queue: Queue[_Work] | None = None
        self._stop = Event()
        self._thread: Thread | None = None
        self._store = None
        self._refresh_id: str | None = None
        self._refresh_timestamp: datetime | None = None
        self._session = "UNKNOWN"
        self._seen: set[str] = set()
        self._accepted = 0
        self._completed = 0
        self._suppressed = 0
        self._rejected = 0
        self._failed = 0
        self._high_water = 0
        self._refresh_count = 0
        self._stopped = not self.enabled
        self._last_error_type: str | None = None
        self._reported_error_types: set[str] = set()
        if not self.enabled:
            return
        try:
            if capacity <= 0:
                raise ValueError("universe telemetry capacity must be positive")
            if self.path.suffix.lower() != ".jsonl":
                raise ValueError("universe telemetry path must be JSONL")
            if self.path.exists() and not self.path.is_file():
                raise IsADirectoryError(str(self.path))
            self._store = store_factory(self.path)
            self._queue = Queue(maxsize=capacity)
            self._thread = Thread(
                target=self._run,
                name="atlas-scanner-universe-research",
                daemon=True,
            )
            self._thread.start()
            self._stopped = False
        except Exception as exc:
            self._fail(exc)
            self._stopped = True

    def begin_refresh(
        self, *, timestamp: datetime, session: str, page_size: int,
    ) -> None:
        try:
            if not self._available():
                return
            _aware(timestamp)
            identity = _digest({
                "provider": "WEBULL_OPENAPI",
                "timestamp": timestamp.isoformat(),
                "session": session,
                "page_size": page_size,
            })
            with self._lock:
                if identity != self._refresh_id:
                    self._refresh_id = identity
                    self._refresh_timestamp = timestamp
                    self._session = session.strip().upper() or "UNKNOWN"
                    self._seen.clear()
                    self._refresh_count += 1
            self.record(
                stage=UniverseAdmissionStage.REFRESH_STARTED,
                outcome=UniverseAdmissionOutcome.STARTED,
                reason="WEBULL_TWO_SOURCE_FIRST_PAGE_REFRESH",
                upstream_fields={"page_index": 1, "page_size": page_size},
                timestamp=timestamp,
            )
        except Exception as exc:
            self._fail(exc)

    def record(
        self,
        *,
        stage: UniverseAdmissionStage,
        outcome: UniverseAdmissionOutcome,
        reason: str,
        screener_identity: str | None = None,
        source_rank: int | None = None,
        raw_symbol: str | None = None,
        normalized_symbol: str | None = None,
        upstream_fields: Mapping[str, object] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        try:
            if not self._available():
                return
            with self._lock:
                refresh_id = self._refresh_id
                refresh_timestamp = self._refresh_timestamp
                session = self._session
            if refresh_id is None or refresh_timestamp is None:
                return
            observed = timestamp or self._clock()
            _aware(observed)
            fields_json = json.dumps(
                dict(upstream_fields or {}), default=str, sort_keys=True,
                separators=(",", ":"),
            )
            body = {
                "refresh_id": refresh_id,
                "stage": stage.value,
                "outcome": outcome.value,
                "reason": reason,
                "screener_identity": screener_identity,
                "source_rank": source_rank,
                "raw_symbol": raw_symbol,
                "normalized_symbol": normalized_symbol,
                "upstream_fields_json": fields_json,
            }
            event_id = _digest(body)
            with self._lock:
                if event_id in self._seen:
                    self._suppressed += 1
                    return
                self._seen.add(event_id)
            event = UniverseAdmissionEvent(
                schema_version=1,
                event_id=event_id,
                refresh_id=refresh_id,
                timestamp=observed,
                session=session,
                trading_date=observed.astimezone(_NEW_YORK).date(),
                provider="WEBULL_OPENAPI",
                screener_identity=screener_identity,
                source_rank=source_rank,
                raw_symbol=_text(raw_symbol),
                normalized_symbol=_upper(normalized_symbol),
                stage=stage,
                outcome=outcome,
                reason=reason.strip().upper(),
                upstream_fields_json=fields_json,
            )
            queue = self._queue
            assert queue is not None
            try:
                queue.put_nowait(_Work(event))
            except Full:
                with self._lock:
                    self._rejected += 1
                return
            with self._lock:
                self._accepted += 1
                self._high_water = max(self._high_water, queue.qsize())
        except Exception as exc:
            self._fail(exc)

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        thread = self._thread
        if thread is None:
            with self._lock:
                self._stopped = True
            return True
        self._stop.set()
        thread.join(max(0.0, timeout_seconds))
        stopped = not thread.is_alive()
        with self._lock:
            self._stopped = stopped
            if not stopped:
                self._failed += 1
        return stopped

    def metrics(self) -> UniverseAdmissionMetrics:
        with self._lock:
            depth = 0 if self._queue is None else self._queue.qsize()
            return UniverseAdmissionMetrics(
                enabled=self.enabled,
                healthy=self._failed == 0,
                accepted=self._accepted,
                completed=self._completed,
                suppressed=self._suppressed,
                rejected=self._rejected,
                failed=self._failed,
                outstanding=self._accepted - self._completed,
                queue_depth=depth,
                queue_high_water=self._high_water,
                refresh_count=self._refresh_count,
                persistence_path=str(self.path),
                stopped=self._stopped,
                last_error_type=self._last_error_type,
            )

    def _available(self) -> bool:
        return self.enabled and self._queue is not None and not self._stop.is_set()

    def _run(self) -> None:
        assert self._queue is not None
        while not self._stop.is_set() or not self._queue.empty():
            try:
                work = self._queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                self._store.append(work.event)
            except Exception as exc:
                self._fail(exc)
            finally:
                with self._lock:
                    self._completed += 1
                self._queue.task_done()
        try:
            self._store.close()
        except Exception as exc:
            self._fail(exc)
        with self._lock:
            self._stopped = True

    def _fail(self, exc: Exception) -> None:
        error_type = type(exc).__name__
        with self._lock:
            self._failed += 1
            self._last_error_type = error_type
            first_report = error_type not in self._reported_error_types
            self._reported_error_types.add(error_type)
        if first_report:
            _LOGGER.warning(
                "scanner universe research telemetry degraded: %s",
                error_type,
            )


def _digest(value: Mapping[str, object]) -> str:
    return sha256(json.dumps(
        dict(value), default=str, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("universe telemetry requires timezone-aware timestamps")


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _upper(value: object | None) -> str | None:
    result = _text(value)
    return None if result is None else result.upper()


__all__ = ["ScannerUniverseAdmissionObserver"]

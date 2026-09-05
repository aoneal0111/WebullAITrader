"""Low-overhead memory/cardinality sampling for research soaks.

This module has no trading or selection authority.  Sampling is opt-in and
all state/queues are bounded; failures are isolated from callers.
"""

from __future__ import annotations

import json
import os
import threading
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Full, Queue
from time import monotonic
from typing import Callable, Mapping


@dataclass(frozen=True, slots=True)
class MemoryDiagnosticSnapshot:
    timestamp: datetime
    rss_bytes: int | None
    private_bytes: int | None
    thread_count: int
    metrics: tuple[tuple[str, int], ...] = ()
    tracemalloc_current_bytes: int | None = None
    tracemalloc_peak_bytes: int | None = None
    tracemalloc_top: tuple[tuple[str, int, int], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "rss_bytes": self.rss_bytes,
            "private_bytes": self.private_bytes,
            "thread_count": self.thread_count,
            "metrics": dict(self.metrics),
            "tracemalloc_current_bytes": self.tracemalloc_current_bytes,
            "tracemalloc_peak_bytes": self.tracemalloc_peak_bytes,
            "tracemalloc_top": [
                {"location": location, "size_bytes": size, "count": count}
                for location, size, count in self.tracemalloc_top
            ],
        }


MetricProvider = Callable[[], Mapping[str, int]]


class MemoryObservability:
    """Optional sampler and asynchronous JSONL sidecar writer."""

    def __init__(self, providers: Mapping[str, MetricProvider] | None = None,
                 *, enabled: bool | None = None, path: str | Path | None = None,
                 interval_seconds: float | None = None,
                 tracemalloc_enabled: bool | None = None,
                 queue_capacity: int = 8, top_allocations: int = 10) -> None:
        self.enabled = _env_bool("ATLAS_MEMORY_OBSERVABILITY_ENABLED", False) if enabled is None else bool(enabled)
        self.tracemalloc_enabled = _env_bool("ATLAS_MEMORY_TRACEMALLOC_ENABLED", False) if tracemalloc_enabled is None else bool(tracemalloc_enabled)
        self.interval_seconds = max(30.0, float(interval_seconds if interval_seconds is not None else os.getenv("ATLAS_MEMORY_OBSERVABILITY_INTERVAL_SECONDS", "60")))
        self.path = Path(path or os.getenv("ATLAS_MEMORY_OBSERVABILITY_PATH", "memory-observability.jsonl"))
        if queue_capacity <= 0 or top_allocations <= 0:
            raise ValueError("diagnostic bounds must be positive")
        self._providers = dict(providers or {})
        self._queue: Queue[MemoryDiagnosticSnapshot | None] = Queue(maxsize=queue_capacity)
        self._top_allocations = top_allocations
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._writer: threading.Thread | None = None
        self._failures = 0
        self._dropped = 0
        self._lifecycle: dict[str, int] = {}

    def record_lifecycle(self, event: str) -> None:
        """Record a bounded session/lifecycle marker for later correlation."""
        key = str(event).strip().lower().replace(" ", "_")
        if not key:
            return
        self._lifecycle[key] = self._lifecycle.get(key, 0) + 1

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        try:
            if self.tracemalloc_enabled and not tracemalloc.is_tracing():
                tracemalloc.start(10)
            self._writer = threading.Thread(target=self._write_loop, name="atlas-memory-diagnostics-writer", daemon=True)
            self._thread = threading.Thread(target=self._sample_loop, name="atlas-memory-diagnostics", daemon=True)
            self._writer.start()
            self._thread.start()
        except Exception:
            self._failures += 1
            self._thread = self._writer = None

    def sample(self) -> MemoryDiagnosticSnapshot | None:
        """Collect one bounded snapshot and enqueue it without blocking."""
        if not self.enabled:
            return None
        try:
            values: dict[str, int] = {}
            for name, provider in tuple(self._providers.items()):
                try:
                    for key, value in provider().items():
                        values[f"{name}_{key}"] = max(0, int(value))
                except Exception:
                    self._failures += 1
            for key, value in tuple(self._lifecycle.items()):
                values[f"lifecycle_{key}"] = value
            current = peak = None
            top: tuple[tuple[str, int, int], ...] = ()
            if self.tracemalloc_enabled and tracemalloc.is_tracing():
                current, peak = tracemalloc.get_traced_memory()
                stats = tracemalloc.take_snapshot().statistics("traceback")[: self._top_allocations]
                top = tuple((str(item.traceback[0]), item.size, item.count) for item in stats)
            snapshot = MemoryDiagnosticSnapshot(datetime.now(UTC), *_process_memory(), threading.active_count(), tuple(sorted(values.items())), current, peak, top)
            try:
                self._queue.put_nowait(snapshot)
            except Full:
                self._dropped += 1
            return snapshot
        except Exception:
            self._failures += 1
            return None

    def close(self, *, timeout_seconds: float = 2.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        writer = self._writer
        if writer is not None:
            writer.join(timeout_seconds)
        return (thread is None or not thread.is_alive()) and (writer is None or not writer.is_alive())

    def metrics(self) -> Mapping[str, int]:
        return {"queue_depth": self._queue.qsize(), "failures": self._failures, "drops": self._dropped}

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.sample()

    def _write_loop(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                while True:
                    item = self._queue.get()
                    try:
                        if item is None:
                            return
                        handle.write(json.dumps(item.to_dict(), separators=(",", ":")) + "\n")
                        handle.flush()
                    finally:
                        self._queue.task_done()
        except Exception:
            self._failures += 1


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _process_memory() -> tuple[int | None, int | None]:
    try:
        import psutil  # type: ignore
        info = psutil.Process().memory_info()
        return int(info.rss), int(getattr(info, "private", 0) or 0) or None
    except Exception:
        return None, None


def summarize_jsonl(path: str | Path) -> dict[str, object]:
    """Summarize a diagnostic JSONL without implying causation."""
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return {"samples": 0}
    def extrema(key: str) -> tuple[object, object]:
        values = [row[key] for row in rows if row.get(key) is not None]
        return (values[0], values[-1]) if values else (None, None)
    return {"samples": len(rows), "start_timestamp": rows[0].get("timestamp"), "end_timestamp": rows[-1].get("timestamp"), "rss_start_end": extrema("rss_bytes"), "private_start_end": extrema("private_bytes"), "thread_range": (min(row.get("thread_count", 0) for row in rows), max(row.get("thread_count", 0) for row in rows))}


__all__ = ["MemoryDiagnosticSnapshot", "MemoryObservability", "summarize_jsonl"]

"""Low-overhead memory/cardinality sampling for research soaks.

This module has no trading or selection authority.  Sampling is opt-in and
all state/queues are bounded; failures are isolated from callers.
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
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
    metrics: tuple[tuple[str, object], ...] = ()
    tracemalloc_current_bytes: int | None = None
    tracemalloc_peak_bytes: int | None = None
    tracemalloc_top: tuple[tuple[str, str, int, int, int], ...] = ()
    tracemalloc_snapshot_captured: bool = False
    gc_generation_counts: tuple[int, int, int] = (0, 0, 0)
    gc_collection_stats: tuple[tuple[int, int, int], ...] = ()
    gc_tracked_objects: int | None = None
    python_allocated_blocks: int | None = None

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
                {
                    "location": location,
                    "filename": filename,
                    "line_number": line_number,
                    "size_bytes": size,
                    "count": count,
                }
                for location, filename, line_number, size, count
                in self.tracemalloc_top
            ],
            "tracemalloc_snapshot_captured": self.tracemalloc_snapshot_captured,
            "gc_generation_counts": list(self.gc_generation_counts),
            "gc_collection_stats": [
                {
                    "collections": collections,
                    "collected": collected,
                    "uncollectable": uncollectable,
                }
                for collections, collected, uncollectable
                in self.gc_collection_stats
            ],
            "gc_tracked_objects": self.gc_tracked_objects,
            "python_allocated_blocks": self.python_allocated_blocks,
        }


MetricProvider = Callable[[], Mapping[str, object]]


class MemoryObservability:
    """Optional sampler and asynchronous JSONL sidecar writer."""

    def __init__(self, providers: Mapping[str, MetricProvider] | None = None,
                 *, enabled: bool | None = None, path: str | Path | None = None,
                 interval_seconds: float | None = None,
                 tracemalloc_enabled: bool | None = None,
                 tracemalloc_snapshot_interval_seconds: float | None = None,
                 gc_tracked_objects_enabled: bool | None = None,
                 queue_capacity: int = 8, top_allocations: int = 10) -> None:
        self.enabled = _env_bool("ATLAS_MEMORY_OBSERVABILITY_ENABLED", False) if enabled is None else bool(enabled)
        self.tracemalloc_enabled = _env_bool("ATLAS_MEMORY_TRACEMALLOC_ENABLED", False) if tracemalloc_enabled is None else bool(tracemalloc_enabled)
        self.interval_seconds = max(30.0, float(interval_seconds if interval_seconds is not None else os.getenv("ATLAS_MEMORY_OBSERVABILITY_INTERVAL_SECONDS", "60")))
        self.tracemalloc_snapshot_interval_seconds = max(
            self.interval_seconds,
            float(
                tracemalloc_snapshot_interval_seconds
                if tracemalloc_snapshot_interval_seconds is not None
                else os.getenv(
                    "ATLAS_MEMORY_TRACEMALLOC_SNAPSHOT_INTERVAL_SECONDS",
                    "600",
                )
            ),
        )
        self.gc_tracked_objects_enabled = (
            _env_bool("ATLAS_MEMORY_GC_TRACKED_OBJECTS_ENABLED", False)
            if gc_tracked_objects_enabled is None
            else bool(gc_tracked_objects_enabled)
        )
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
        self._process_memory_unavailable = 0
        self._process_memory_query_failures = 0
        self._tracemalloc_snapshot_failures = 0
        self._next_tracemalloc_snapshot_at = 0.0
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
            values: dict[str, object] = {}
            for name, provider in tuple(self._providers.items()):
                try:
                    for key, value in provider().items():
                        if value is None or isinstance(value, str):
                            values[f"{name}_{key}"] = value
                        elif isinstance(value, bool):
                            values[f"{name}_{key}"] = int(value)
                        elif isinstance(value, int):
                            values[f"{name}_{key}"] = max(0, value)
                        elif isinstance(value, float):
                            values[f"{name}_{key}"] = max(0.0, value)
                except Exception:
                    self._failures += 1
            for key, value in tuple(self._lifecycle.items()):
                values[f"lifecycle_{key}"] = value
            current = peak = None
            top: tuple[tuple[str, str, int, int, int], ...] = ()
            tracemalloc_snapshot_captured = False
            if self.tracemalloc_enabled and tracemalloc.is_tracing():
                current, peak = tracemalloc.get_traced_memory()
                now = monotonic()
                if now >= self._next_tracemalloc_snapshot_at:
                    try:
                        allocation_snapshot = tracemalloc.take_snapshot()
                        try:
                            stats = allocation_snapshot.statistics("traceback")[
                                : self._top_allocations
                            ]
                            top = tuple(
                                (
                                    str(item.traceback[0]),
                                    item.traceback[0].filename,
                                    item.traceback[0].lineno,
                                    item.size,
                                    item.count,
                                )
                                for item in stats
                            )
                            tracemalloc_snapshot_captured = True
                        finally:
                            del allocation_snapshot
                    except Exception:
                        self._tracemalloc_snapshot_failures += 1
                    finally:
                        self._next_tracemalloc_snapshot_at = (
                            now + self.tracemalloc_snapshot_interval_seconds
                        )
            gc_counts = tuple(int(value) for value in gc.get_count())
            gc_stats = tuple(
                (
                    int(item.get("collections", 0)),
                    int(item.get("collected", 0)),
                    int(item.get("uncollectable", 0)),
                )
                for item in gc.get_stats()
            )
            tracked_objects = (
                len(gc.get_objects())
                if self.gc_tracked_objects_enabled
                else None
            )
            allocated_blocks_source = getattr(sys, "getallocatedblocks", None)
            allocated_blocks = (
                max(0, int(allocated_blocks_source()))
                if callable(allocated_blocks_source)
                else None
            )
            snapshot = MemoryDiagnosticSnapshot(
                datetime.now(UTC),
                *_process_memory(self._record_process_memory_failure),
                threading.active_count(),
                tuple(sorted(values.items())),
                current,
                peak,
                top,
                tracemalloc_snapshot_captured,
                gc_counts,
                gc_stats,
                tracked_objects,
                allocated_blocks,
            )
            try:
                self._queue.put_nowait(snapshot)
            except Full:
                self._dropped += 1
            return snapshot
        except Exception:
            self._failures += 1
            return None

    def close(self, *, timeout_seconds: float = 2.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        deadline = monotonic() + timeout_seconds
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(max(0.0, deadline - monotonic()))
        try:
            self._queue.put(None, timeout=max(0.0, deadline - monotonic()))
        except Full:
            pass
        writer = self._writer
        if writer is not None:
            writer.join(max(0.0, deadline - monotonic()))
        return (thread is None or not thread.is_alive()) and (writer is None or not writer.is_alive())

    def metrics(self) -> Mapping[str, int]:
        return {
            "queue_depth": self._queue.qsize(),
            "failures": self._failures,
            "drops": self._dropped,
            "process_memory_unavailable": self._process_memory_unavailable,
            "process_memory_query_failures": self._process_memory_query_failures,
            "tracemalloc_snapshot_failures": self._tracemalloc_snapshot_failures,
        }

    def _record_process_memory_failure(self, reason: str) -> None:
        if reason == "unavailable":
            self._process_memory_unavailable += 1
        else:
            self._process_memory_query_failures += 1

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


class _ProcessMemoryCountersEx(ctypes.Structure):
    """Windows PROCESS_MEMORY_COUNTERS_EX layout."""

    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _windows_process_memory() -> tuple[int, int]:
    """Return current Windows working set and private commit, in bytes."""
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCountersEx),
        ctypes.c_ulong,
    )
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.WorkingSetSize), int(counters.PrivateUsage)


def _process_memory(
    report_failure: Callable[[str], None] | None = None,
) -> tuple[int | None, int | None]:
    """Collect process memory without allowing diagnostics to affect callers."""
    if os.name != "nt":
        if report_failure is not None:
            report_failure("unavailable")
        return None, None
    try:
        return _windows_process_memory()
    except Exception:
        if report_failure is not None:
            report_failure("query_failed")
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

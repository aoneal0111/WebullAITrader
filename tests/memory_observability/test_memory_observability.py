from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
import gc
import os
import weakref

import pytest

from app.memory_observability import MemoryObservability, summarize_jsonl
from app.memory_observability import runtime as memory_runtime


def test_disabled_by_default_has_no_sampling_or_side_effect(tmp_path):
    path = tmp_path / "memory.jsonl"
    diagnostics = MemoryObservability({"synthetic": lambda: {"events": 10}}, path=path)
    assert diagnostics.sample() is None
    assert not path.exists()


def test_snapshot_counts_match_bounded_container_sizes(tmp_path):
    state = {"events": 0, "symbols": set(), "bars": {}}

    def metrics():
        return {"event_count": state["events"],
                "unique_symbols": len(state["symbols"]),
                "total_bars": sum(state["bars"].values()),
                "max_bars_per_symbol": max(state["bars"].values(), default=0)}

    diagnostics = MemoryObservability({"synthetic": metrics}, enabled=True,
                                      path=tmp_path / "memory.jsonl")
    for index in range(10_000):
        symbol = f"S{index % 100}"
        state["events"] += 1
        state["symbols"].add(symbol)
        state["bars"][symbol] = min(64, state["bars"].get(symbol, 0) + 1)
    snapshot = diagnostics.sample()
    assert snapshot is not None
    assert dict(snapshot.metrics) == {
        "synthetic_event_count": 10_000,
        "synthetic_max_bars_per_symbol": 64,
        "synthetic_total_bars": 6_400,
        "synthetic_unique_symbols": 100,
    }
    with pytest.raises(FrozenInstanceError):
        snapshot.thread_count = 0
    assert diagnostics.close()


def test_sidecar_is_async_bounded_and_summary_is_read_only(tmp_path):
    path = tmp_path / "memory.jsonl"
    diagnostics = MemoryObservability({"source": lambda: {"count": 3}}, enabled=True,
                                      path=path, interval_seconds=30, queue_capacity=1)
    diagnostics.start()
    assert diagnostics.sample() is not None
    assert diagnostics.close()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows and rows[0]["metrics"]["source_count"] == 3
    summary = summarize_jsonl(path)
    assert summary["samples"] == len(rows)
    assert "rss_start_end" in summary


def test_provider_and_invalid_writer_failures_are_isolated(tmp_path):
    diagnostics = MemoryObservability(
        {"bad": lambda: (_ for _ in ()).throw(RuntimeError("boom"))},
        enabled=True, path=tmp_path / "memory.jsonl")
    assert diagnostics.sample() is not None
    assert diagnostics.metrics()["failures"] == 1
    invalid = MemoryObservability({"ok": lambda: {"count": 1}}, enabled=True,
                                 path=tmp_path / "missing" / "memory.jsonl")
    invalid.start()
    assert invalid.sample() is not None
    assert invalid.close()


def test_repeated_identical_events_are_counted_without_semantic_assumptions():
    values = {"events": 0, "unique_symbols": 0}
    diagnostics = MemoryObservability(
        {"stream": lambda: dict(values)}, enabled=True,
        interval_seconds=30)
    for _ in range(10_000):
        values["events"] += 1
    first = diagnostics.sample()
    assert first is not None and dict(first.metrics)["stream_events"] == 10_000
    values["unique_symbols"] = 1
    second = diagnostics.sample()
    assert second is not None and dict(second.metrics)["stream_unique_symbols"] == 1
    diagnostics.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process-memory contract")
def test_windows_process_memory_returns_current_positive_values():
    rss_bytes, private_bytes = memory_runtime._process_memory()

    assert isinstance(rss_bytes, int) and rss_bytes > 0
    assert isinstance(private_bytes, int) and private_bytes > 0


def test_process_memory_values_are_preserved_by_snapshot_serialization(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(memory_runtime.os, "name", "nt")
    monkeypatch.setattr(
        memory_runtime, "_windows_process_memory", lambda: (12_345, 67_890)
    )
    diagnostics = MemoryObservability(enabled=True, path=tmp_path / "memory.jsonl")

    snapshot = diagnostics.sample()

    assert snapshot is not None
    assert snapshot.to_dict()["rss_bytes"] == 12_345
    assert snapshot.to_dict()["private_bytes"] == 67_890
    assert diagnostics.metrics()["process_memory_query_failures"] == 0
    diagnostics.close()


def test_process_memory_query_failure_is_contained_and_classified(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(memory_runtime.os, "name", "nt")

    def fail_query():
        raise OSError("synthetic query failure")

    monkeypatch.setattr(memory_runtime, "_windows_process_memory", fail_query)
    diagnostics = MemoryObservability(enabled=True, path=tmp_path / "memory.jsonl")

    snapshot = diagnostics.sample()

    assert snapshot is not None
    assert snapshot.rss_bytes is None
    assert snapshot.private_bytes is None
    assert diagnostics.metrics()["process_memory_query_failures"] == 1
    assert diagnostics.metrics()["process_memory_unavailable"] == 0
    diagnostics.close()


def test_unsupported_process_memory_mechanism_is_classified(monkeypatch):
    failures: list[str] = []
    monkeypatch.setattr(memory_runtime.os, "name", "unsupported")

    assert memory_runtime._process_memory(failures.append) == (None, None)
    assert failures == ["unavailable"]


def test_gc_and_allocated_block_metrics_are_emitted_without_forced_collection(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(memory_runtime.gc, "get_count", lambda: (1, 2, 3))
    monkeypatch.setattr(memory_runtime.gc, "get_stats", lambda: [
        {"collections": 4, "collected": 5, "uncollectable": 6},
        {"collections": 7, "collected": 8, "uncollectable": 9},
        {"collections": 10, "collected": 11, "uncollectable": 12},
    ])
    monkeypatch.setattr(
        memory_runtime.gc,
        "get_objects",
        lambda: (_ for _ in ()).throw(AssertionError("opt-in only")),
    )
    monkeypatch.setattr(
        memory_runtime.gc,
        "collect",
        lambda: (_ for _ in ()).throw(AssertionError("must not collect")),
    )
    monkeypatch.setattr(memory_runtime.sys, "getallocatedblocks", lambda: 1234)
    diagnostics = MemoryObservability(
        enabled=True,
        path=tmp_path / "memory.jsonl",
        gc_tracked_objects_enabled=False,
    )

    snapshot = diagnostics.sample()

    assert snapshot is not None
    assert snapshot.gc_generation_counts == (1, 2, 3)
    assert snapshot.gc_collection_stats == ((4, 5, 6), (7, 8, 9), (10, 11, 12))
    assert snapshot.gc_tracked_objects is None
    assert snapshot.python_allocated_blocks == 1234
    assert diagnostics.close()


def test_gc_tracked_object_count_is_explicitly_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(memory_runtime.gc, "get_objects", lambda: [1, 2, 3])
    diagnostics = MemoryObservability(
        enabled=True,
        path=tmp_path / "memory.jsonl",
        gc_tracked_objects_enabled=True,
    )

    snapshot = diagnostics.sample()

    assert snapshot is not None and snapshot.gc_tracked_objects == 3
    assert diagnostics.close()


def test_tracemalloc_disabled_never_takes_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        memory_runtime.tracemalloc,
        "take_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("snapshot work disabled")),
    )
    diagnostics = MemoryObservability(
        enabled=True,
        tracemalloc_enabled=False,
        path=tmp_path / "memory.jsonl",
    )

    snapshot = diagnostics.sample()

    assert snapshot is not None
    assert snapshot.tracemalloc_current_bytes is None
    assert snapshot.tracemalloc_top == ()
    assert not snapshot.tracemalloc_snapshot_captured
    assert diagnostics.close()


def test_tracemalloc_summary_is_bounded_slow_cadence_and_snapshot_is_released(
    monkeypatch, tmp_path,
) -> None:
    tracing = {"active": False}
    snapshot_references: list[weakref.ReferenceType] = []

    class Frame:
        def __init__(self, index: int) -> None:
            self.filename = f"site-{index}.py"
            self.lineno = index + 10

        def __str__(self) -> str:
            return f"{self.filename}:{self.lineno}"

    class Stat:
        def __init__(self, index: int) -> None:
            self.traceback = (Frame(index),)
            self.size = 100 - index
            self.count = index + 1

    class AllocationSnapshot:
        def statistics(self, _grouping):
            return [Stat(index) for index in range(5)]

    def take_snapshot():
        result = AllocationSnapshot()
        snapshot_references.append(weakref.ref(result))
        return result

    monkeypatch.setattr(memory_runtime.tracemalloc, "is_tracing", lambda: tracing["active"])
    monkeypatch.setattr(memory_runtime.tracemalloc, "start", lambda _frames: tracing.update(active=True))
    monkeypatch.setattr(memory_runtime.tracemalloc, "get_traced_memory", lambda: (111, 222))
    monkeypatch.setattr(memory_runtime.tracemalloc, "take_snapshot", take_snapshot)
    diagnostics = MemoryObservability(
        enabled=True,
        tracemalloc_enabled=True,
        tracemalloc_snapshot_interval_seconds=600,
        top_allocations=2,
        path=tmp_path / "memory.jsonl",
    )
    diagnostics.start()

    first = diagnostics.sample()
    second = diagnostics.sample()

    assert first is not None and first.tracemalloc_snapshot_captured
    assert first.tracemalloc_current_bytes == 111
    assert first.tracemalloc_peak_bytes == 222
    assert len(first.tracemalloc_top) == 2
    serialized = first.to_dict()["tracemalloc_top"]
    assert serialized[0] == {
        "location": "site-0.py:10",
        "filename": "site-0.py",
        "line_number": 10,
        "size_bytes": 100,
        "count": 1,
    }
    assert second is not None and not second.tracemalloc_snapshot_captured
    assert second.tracemalloc_top == ()
    gc.collect()
    assert snapshot_references and snapshot_references[0]() is None
    assert not hasattr(diagnostics, "_tracemalloc_snapshots")
    assert diagnostics.close()


def test_tracemalloc_snapshot_failure_is_isolated(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(memory_runtime.tracemalloc, "is_tracing", lambda: True)
    monkeypatch.setattr(memory_runtime.tracemalloc, "get_traced_memory", lambda: (11, 22))
    monkeypatch.setattr(
        memory_runtime.tracemalloc,
        "take_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    diagnostics = MemoryObservability(
        enabled=True,
        tracemalloc_enabled=True,
        path=tmp_path / "memory.jsonl",
    )

    snapshot = diagnostics.sample()

    assert snapshot is not None
    assert snapshot.tracemalloc_current_bytes == 11
    assert not snapshot.tracemalloc_snapshot_captured
    assert diagnostics.metrics()["tracemalloc_snapshot_failures"] == 1
    assert diagnostics.close()

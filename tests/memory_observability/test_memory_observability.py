from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
import pytest

from app.memory_observability import MemoryObservability, summarize_jsonl


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

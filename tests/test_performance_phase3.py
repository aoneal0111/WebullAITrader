"""Deterministic tests for durable, run-scoped performance evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.performance_diagnostics as diagnostics_module
from app.performance_diagnostics import PerformanceDiagnostics


def _start(tmp_path: Path, *, flush_seconds: float = 0.05) -> PerformanceDiagnostics:
    diagnostics = PerformanceDiagnostics()
    diagnostics.start_run(
        artifact_path=tmp_path / "run.json",
        branch="test-branch",
        application_version="test-version",
        flush_seconds=flush_seconds,
    )
    return diagnostics


def _finish(diagnostics: PerformanceDiagnostics) -> dict[str, object]:
    diagnostics.finish_run()
    path = diagnostics.artifact_path
    assert path is not None and path.exists()
    return json.loads(path.read_text(encoding="utf-8"))


def test_run_identity_and_final_artifact(tmp_path: Path) -> None:
    diagnostics = _start(tmp_path)
    run_id = diagnostics.run_id
    diagnostics.record_startup_stage("process_started")
    artifact = _finish(diagnostics)

    assert artifact["run_id"] == run_id
    assert artifact["status"] == "FINAL"
    assert artifact["schema_version"] == 1
    assert artifact["branch"] == "test-branch"
    assert artifact["application_version"] == "test-version"
    assert artifact["shutdown_at"] is not None


def test_startup_absent_stage_is_null_and_timings_serialize(tmp_path: Path) -> None:
    diagnostics = _start(tmp_path)
    diagnostics.record_startup_stage("runtime_started")
    diagnostics.record_startup_stage("broker_connect_started")
    diagnostics.record_startup_stage("broker_connected")
    artifact = _finish(diagnostics)

    startup = artifact["startup"]
    assert startup["feed_healthy_at"] is None
    assert startup["broker_connect_ms"] is not None


def test_component_queue_and_reconciliation_aggregates_survive_serialization(
    tmp_path: Path,
) -> None:
    diagnostics = _start(tmp_path)
    for duration in (1.0, 2.0, 3.0):
        diagnostics.record_component_duration("warrior.test", duration)
    diagnostics.increment_reconciliation_counter("eligibility_checks", 3)
    diagnostics.increment_reconciliation_counter("executions")
    diagnostics.increment_reconciliation_counter("successes")
    diagnostics.record_market_event_callback(4)
    diagnostics.record_market_event_callback(2)
    diagnostics.record_event_processing_age(12.0)
    artifact = _finish(diagnostics)

    component = artifact["metrics"]["component_timings"]["warrior.test"]
    assert component["calls"] == 3
    assert component["p50_ms"] == 2.0
    assert component["p90_ms"] == 2.0
    assert component["p99_ms"] == 2.0
    assert component["max_ms"] == 3.0
    assert artifact["reconciliation"]["eligibility_checks"] == 3
    assert artifact["reconciliation"]["executions"] == 1
    assert artifact["metrics"]["callback_queue_high_water"] == 4


def test_periodic_flush_is_readable_before_shutdown(tmp_path: Path) -> None:
    diagnostics = _start(tmp_path, flush_seconds=0.05)
    path = diagnostics.artifact_path
    assert path is not None
    for _ in range(30):
        if path.exists():
            break
        __import__("time").sleep(0.02)
    assert path.exists()
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "CHECKPOINT"
    diagnostics.finish_run()


def test_persistence_failure_isolated_from_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostics = _start(tmp_path)

    def fail_replace(*args: object) -> None:
        raise OSError("simulated diagnostics storage failure")

    monkeypatch.setattr(diagnostics_module.os, "replace", fail_replace)
    diagnostics.record_component_duration("slow", 4.0)
    diagnostics.finish_run()
    assert diagnostics.durable_metrics()["write_failures"] >= 1


def test_repeated_flush_remains_bounded_and_does_not_store_payloads(tmp_path: Path) -> None:
    diagnostics = _start(tmp_path)
    for _ in range(200):
        diagnostics.record_component_duration("bounded", 1.0)
        diagnostics.request_checkpoint()
    artifact = _finish(diagnostics)
    path = diagnostics.artifact_path
    assert path is not None and path.stat().st_size < 100_000
    serialized = json.dumps(artifact).lower()
    assert "token" not in serialized
    assert "password" not in serialized


def test_two_runs_have_distinct_artifacts(tmp_path: Path) -> None:
    first = PerformanceDiagnostics()
    first.start_run(artifact_path=tmp_path / "first.json", flush_seconds=0.05)
    first_id = first.run_id
    first.finish_run()

    second = PerformanceDiagnostics()
    second.start_run(artifact_path=tmp_path / "second.json", flush_seconds=0.05)
    second_id = second.run_id
    second.finish_run()

    assert first_id != second_id
    assert (tmp_path / "first.json").exists()
    assert (tmp_path / "second.json").exists()


def test_hot_path_updates_do_not_write_synchronously(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostics = _start(tmp_path)
    writes: list[str] = []
    original = diagnostics._write_durable_artifact

    def observe(status: str) -> None:
        writes.append(status)
        original(status)

    monkeypatch.setattr(diagnostics, "_write_durable_artifact", observe)
    diagnostics.record_market_event_callback(1)
    diagnostics.record_event_processing_age(5.0)
    diagnostics.record_component_duration("market", 1.0)
    assert writes == []
    diagnostics.finish_run()

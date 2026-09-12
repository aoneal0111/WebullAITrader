from datetime import UTC, datetime
import json

from app.trade_intelligence.knowledge import orchestration
from app.trade_intelligence.knowledge.orchestration import ResearchOrchestrator, RunPlan, resolve_corpus_root
import pytest


class FakeDailyClient:
    calls = []
    request_counters = {"requests": 0, "pages": 0, "429": 0, "5xx": 0, "retries": 0}

    def __init__(self, config=None):
        self.config = config

    @classmethod
    def from_environment(cls, **kwargs):
        return cls(kwargs.get("config"))

    def fetch_daily_bars(self, symbols, start, end):
        self.calls.append(tuple(symbols))
        self.request_counters["requests"] += 1
        rows = []
        for symbol in symbols:
            for stamp, opened, close in (("2026-05-29T15:00:00+00:00", 9, 10),
                                         ("2026-06-01T15:00:00+00:00", 10, 11),):
                rows.append({"S": symbol, "t": stamp, "o": opened, "h": close + 1, "l": opened - 1,
                             "c": close, "v": 100000})
        return tuple(rows)

    def close(self):
        pass


def test_preflight_batches_daily_work_and_never_requests_minutes(tmp_path, monkeypatch):
    FakeDailyClient.calls = []
    monkeypatch.setattr(orchestration, "AlpacaHistoricalClient", FakeDailyClient)
    plan = RunPlan("ALPACA", "IEX", datetime(2026, 6, 1).date(), datetime(2026, 6, 30).date(), 5000)
    result = ResearchOrchestrator(plan, root=tmp_path).preflight_daily(tuple(f"S{i:03d}" for i in range(3)))
    assert result["daily_request_batches"] == 1
    assert result["daily_request_counters"]["requests"] == 1
    assert result["minute_requests"] == 0
    assert result["candidate_symbol_days"] == 3
    assert (tmp_path / "candidates" / "candidate_days.jsonl").exists()


def test_execution_plan_loads_authoritative_preflight_without_daily_network(tmp_path, monkeypatch):
    FakeDailyClient.calls = []
    monkeypatch.setattr(orchestration, "AlpacaHistoricalClient", FakeDailyClient)
    plan = RunPlan("ALPACA", "IEX", datetime(2026, 6, 1).date(), datetime(2026, 6, 30).date(), 5000)
    orchestrator = ResearchOrchestrator(plan, root=tmp_path)
    preflight = orchestrator.preflight_daily(tuple(f"S{i:03d}" for i in range(3)))
    calls_after_preflight = len(FakeDailyClient.calls)
    handoff = orchestrator.execution_plan_only(tuple(f"S{i:03d}" for i in range(3)))
    assert handoff["candidate_count"] == preflight["candidate_symbol_days"] == 3
    assert handoff["candidate_artifact_sha256"] == json.loads(
        (tmp_path / "candidates" / "candidate_plan.json").read_text(encoding="utf-8"))["candidate_artifact_sha256"]
    assert len(FakeDailyClient.calls) == calls_after_preflight
    assert handoff["daily_network_requests"] == 0
    assert handoff["minute_requests"] == 0


def test_missing_plan_rebuilds_from_daily_cache_without_network(tmp_path, monkeypatch):
    FakeDailyClient.calls = []
    monkeypatch.setattr(orchestration, "AlpacaHistoricalClient", FakeDailyClient)
    symbols = tuple(f"S{i:03d}" for i in range(3))
    plan = RunPlan("ALPACA", "IEX", datetime(2026, 6, 1).date(), datetime(2026, 6, 30).date(), 5000)
    orchestrator = ResearchOrchestrator(plan, root=tmp_path)
    orchestrator.preflight_daily(symbols)
    (tmp_path / "candidates" / "candidate_plan.json").unlink()
    FakeDailyClient.calls = []
    rebuilt = orchestrator.prepare_candidate_plan(symbols, allow_network=False)
    assert len(rebuilt["candidates"]) == 3
    assert FakeDailyClient.calls == []


def test_missing_daily_batch_refetches_only_that_batch(tmp_path, monkeypatch):
    FakeDailyClient.calls = []
    monkeypatch.setattr(orchestration, "AlpacaHistoricalClient", FakeDailyClient)
    symbols = tuple(f"S{i:03d}" for i in range(3))
    plan = RunPlan("ALPACA", "IEX", datetime(2026, 6, 1).date(), datetime(2026, 6, 30).date(), 5000)
    orchestrator = ResearchOrchestrator(plan, root=tmp_path)
    orchestrator.preflight_daily(symbols)
    (tmp_path / "candidates" / "candidate_plan.json").unlink()
    batch = next((tmp_path / "daily" / "batches").glob("*.jsonl"))
    batch.unlink()
    next((tmp_path / "daily" / "manifests").glob("*.json")).unlink()
    FakeDailyClient.calls = []
    rebuilt = orchestrator.prepare_candidate_plan(symbols, allow_network=True)
    assert len(rebuilt["candidates"]) == 3
    assert len(FakeDailyClient.calls) == 1


def test_new_tranche_defaults_to_corpus_and_pointer_selects_repaired(tmp_path):
    assert resolve_corpus_root(tmp_path / "new") == tmp_path / "new" / "corpus"
    repaired = tmp_path / "tranche" / "corpus_repaired"
    repaired.mkdir(parents=True)
    (repaired / "mined_partitions.jsonl").write_text("", encoding="utf-8")
    (repaired / "manifest.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
    (repaired.parent / "corpus_pointer.json").write_text(json.dumps({
        "canonical_corpus_path": "corpus_repaired", "validation_status": "PASS"}), encoding="utf-8")
    assert resolve_corpus_root(repaired.parent, validate=False) == repaired


def test_invalid_continuation_pointer_fails_closed(tmp_path):
    root = tmp_path / "tranche"
    root.mkdir()
    (root / "corpus_repaired").mkdir()
    (root / "corpus_pointer.json").write_text(json.dumps({
        "canonical_corpus_path": "corpus_repaired", "validation_status": "PASS"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="MISSING_MINED_PARTITION_INDEX"):
        resolve_corpus_root(root, validate=False)

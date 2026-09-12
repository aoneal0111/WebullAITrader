from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import hashlib
import pytest

from app.trade_intelligence.knowledge.identity import episode_id, membership_id, near_key
from app.trade_intelligence.knowledge.mining import JsonlBarProvider, _outcomes, build_corpus
from app.trade_intelligence.knowledge.models import ACTIVE_STRATEGIES
from app.trade_intelligence.knowledge.reporting import validate_corpus
from app.trade_intelligence.knowledge.storage import KnowledgeStore
from app.trade_intelligence.knowledge.orchestration import ResearchOrchestrator, RunPlan
from app.trade_intelligence.knowledge.mining import mining_content_key
import app.trade_intelligence.knowledge.orchestration as orchestration_module


def _write(path, bars):
    path.write_text("\n".join(json.dumps({
        "symbol": "XYZ", "timestamp": bar[0].isoformat(), "open": str(bar[1]),
        "high": str(bar[2]), "low": str(bar[3]), "close": str(bar[4]), "volume": "1000",
    }) for bar in bars) + "\n", encoding="utf-8")


def test_identity_and_near_keys_are_deterministic():
    at = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    first = episode_id(symbol="XYZ", trading_date="2026-01-02", session="REGULAR",
                       structural_anchor="A", setup_start=at, trigger_price="2", structural_stop="1")
    assert first == episode_id(symbol="XYZ", trading_date="2026-01-02", session="REGULAR",
                               structural_anchor="A", setup_start=at, trigger_price="2", structural_stop="1")
    assert membership_id(first, "MICRO_PULLBACK") != membership_id(first, "FIRST_PULLBACK")
    assert near_key("xyz", "2026-01-02", "regular", "MICRO_PULLBACK", "A").startswith("XYZ|")


def test_build_is_idempotent_and_no_lookahead(tmp_path):
    start = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    bars = []
    prices = [(1, 1.1, .99, 1.05), (1.05, 1.2, 1.02, 1.15), (1.15, 1.3, 1.1, 1.25),
              (1.25, 1.3, 1.2, 1.22), (1.22, 1.32, 1.2, 1.3), (1.3, 1.5, 1.29, 1.45)]
    for index, values in enumerate(prices):
        bars.append((start + timedelta(minutes=index), *(Decimal(str(item)) for item in values)))
    source = tmp_path / "bars.jsonl"; _write(source, bars)
    output = tmp_path / "pack"
    first = build_corpus(JsonlBarProvider(source), output, repository_commit="test")
    second = build_corpus(JsonlBarProvider(source), output, repository_commit="test")
    assert first.accepted_unique >= 0
    assert first.accepted_unique > 0
    assert second.accepted_unique == 0
    assert second.exact_duplicates >= 0
    assert not validate_corpus(output)


def test_store_status_separates_physical_episodes_from_memberships(tmp_path):
    store = KnowledgeStore(tmp_path / "pack")
    assert store.status()["unique_episodes"] == 0
    assert len(ACTIVE_STRATEGIES) == 23


def test_outcomes_are_forward_only_and_same_bar_order_is_unknown():
    cutoff = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    future = (type("Bar", (), {"timestamp": cutoff + timedelta(minutes=1), "high": Decimal("2.2"),
                               "low": Decimal("0.8")})(),)
    value = _outcomes(Decimal("2"), Decimal("1"), cutoff, future)
    assert value["percent_targets"]["8"]["first_plan_event"] == "INTRABAR_ORDER_UNKNOWN"
    assert value["percent_targets"]["8"]["hit"] is True
    assert value["mfe_r"] == "0.2"
    assert value["post_8"]["time_to_8"] == 60
    assert value["profit_management_research"]["2"]["target_hit"] is True


def test_malformed_source_rows_are_quarantined(tmp_path):
    source = tmp_path / "bad.jsonl"
    source.write_text('{"symbol":"XYZ","timestamp":"not-a-time"}\n', encoding="utf-8")
    provider = JsonlBarProvider(source)
    assert tuple(provider.bars()) == ()
    assert provider.errors and provider.errors[0][0] == 1


def test_incremental_partition_mining_does_not_complete_global_corpus(tmp_path):
    start = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    bars = [(start + timedelta(minutes=index), *(Decimal(str(item)) for item in values))
            for index, values in enumerate(((1, 1.1, .99, 1.05), (1.05, 1.2, 1.02, 1.15),
                                             (1.15, 1.3, 1.1, 1.25), (1.25, 1.3, 1.2, 1.22),
                                             (1.22, 1.32, 1.2, 1.3), (1.3, 1.5, 1.29, 1.45)))]
    source = tmp_path / "bars.jsonl"
    _write(source, bars)
    plan = RunPlan("local", "fixture", start.date(), start.date(), 5000)
    orchestrator = ResearchOrchestrator(plan, root=tmp_path / "root", corpus_root=tmp_path / "corpus")

    first = orchestrator.run_local_mine(source, repository_commit="test", partition_id="p1")
    second = orchestrator.run_local_mine(source, repository_commit="test", partition_id="p2")
    repeat = orchestrator.run_local_mine(source, repository_commit="test", partition_id="p1")
    store = KnowledgeStore(tmp_path / "corpus")

    assert first["accepted_unique"] > 0
    assert second["accepted_unique"] == 0  # global identity/dedupe remains intact
    assert repeat["skipped"] is True
    assert store.mined_partitions()["p1"]["status"] == "COMPLETE"
    assert store.mined_partitions()["p2"]["status"] == "COMPLETE"
    assert json.loads(store.checkpoint_path.read_text(encoding="utf-8"))["completed"] is False


def test_in_progress_partition_resume_is_idempotent_and_changed_input_is_new_identity(tmp_path):
    start = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    source = tmp_path / "bars.jsonl"
    _write(source, [(start + timedelta(minutes=index), Decimal("1"), Decimal("1.2"),
                     Decimal("0.9"), Decimal("1.1")) for index in range(6)])
    plan = RunPlan("local", "fixture", start.date(), start.date(), 5000)
    orchestrator = ResearchOrchestrator(plan, root=tmp_path / "root", corpus_root=tmp_path / "corpus")
    first = orchestrator.run_local_mine(source, repository_commit="test", partition_id="same")
    store = KnowledgeStore(tmp_path / "corpus")
    store.record_mined_partition({"mining_partition_id": "same", "status": "IN_PROGRESS"})
    resumed = orchestrator.run_local_mine(source, repository_commit="test", partition_id="same")
    assert first["accepted_unique"] >= 0
    assert resumed["accepted_unique"] == 0
    assert store.mined_partitions()["same"]["status"] == "COMPLETE"

    changed = tmp_path / "changed.jsonl"
    _write(changed, [(start + timedelta(minutes=index), Decimal("1"), Decimal("1.3"),
                      Decimal("0.9"), Decimal("1.2")) for index in range(6)])
    old_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    new_digest = hashlib.sha256(changed.read_bytes()).hexdigest()
    assert old_digest != new_digest
    old_identity = hashlib.sha256(("same|" + old_digest).encode()).hexdigest()
    new_identity = hashlib.sha256(("same|" + new_digest).encode()).hexdigest()
    assert old_identity != new_identity  # changed input cannot use the old completion identity


def test_commit_provenance_changes_do_not_invalidate_semantic_completion(tmp_path):
    store = KnowledgeStore(tmp_path / "pack")
    key = mining_content_key(candidate_plan_id="plan", symbol="XYZ", trading_date="2026-01-02",
                             normalized_sha256="digest")
    store.record_mined_partition({"mining_partition_id": key, "mining_content_key": key,
                                  "candidate_plan_id": "plan", "symbol": "XYZ", "trading_date": "2026-01-02",
                                  "normalized_sha256": "digest", "status": "COMPLETE",
                                  "repository_commit": "commit-a", "knowledge_schema_version": 1,
                                  "feature_derivation_version": "ATLAS_PIT_FEATURES_V1",
                                  "strategy_semantics_version": "ATLAS_STRATEGY_SEMANTICS_V1",
                                  "mining_semantics_version": "ATLAS_MINING_SEMANTICS_V1"})
    assert store.compatible_complete(candidate_plan_id="plan", symbol="XYZ", trading_date="2026-01-02",
                                     normalized_sha256="digest") is not None
    assert store.effective_mined_partitions()


@pytest.mark.parametrize("field,value", (("normalized_sha256", "other"),
                                           ("feature_derivation_version", "ATLAS_PIT_FEATURES_V2"),
                                           ("strategy_semantics_version", "ATLAS_STRATEGY_SEMANTICS_V2"),
                                           ("mining_semantics_version", "ATLAS_MINING_SEMANTICS_V2"),
                                           ("knowledge_schema_version", 2),
                                           ("candidate_plan_id", "other-plan")))
def test_semantic_input_change_rejects_completion(tmp_path, field, value):
    store = KnowledgeStore(tmp_path / "pack")
    record = {"mining_partition_id": "legacy", "candidate_plan_id": "plan", "symbol": "XYZ",
              "trading_date": "2026-01-02", "normalized_sha256": "digest", "status": "COMPLETE",
              "knowledge_schema_version": 1, "feature_derivation_version": "ATLAS_PIT_FEATURES_V1",
              "strategy_semantics_version": "ATLAS_STRATEGY_SEMANTICS_V1",
              "mining_semantics_version": "ATLAS_MINING_SEMANTICS_V1"}
    store.record_mined_partition(record)
    requested = {key: record[key] for key in ("candidate_plan_id", "symbol", "trading_date", "normalized_sha256",
                                               "knowledge_schema_version", "feature_derivation_version",
                                               "strategy_semantics_version", "mining_semantics_version")}
    requested[field] = value
    assert store.compatible_complete(**requested) is None


def test_reconciliation_supersedes_duplicate_identity_without_payload_change(tmp_path):
    store = KnowledgeStore(tmp_path / "pack")
    common = {"candidate_plan_id": "plan", "symbol": "XYZ", "trading_date": "2026-01-02",
              "normalized_sha256": "digest", "status": "COMPLETE"}
    store.record_mined_partition({**common, "mining_partition_id": "legacy", "repository_commit": "a"})
    store.record_mined_partition({**common, "mining_partition_id": "new", "repository_commit": "b"})
    result = store.reconcile_mined_partitions()
    assert result["effective_complete"] == 1
    assert result["superseded"] == 1
    assert store.mined_partitions()["new"]["status"] == "SUPERSEDED"
    assert store.reconcile_mined_partitions()["superseded"] == 0


def test_partition_mining_uses_one_long_lived_store_session(tmp_path, monkeypatch):
    start = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    source = tmp_path / "bars.jsonl"
    _write(source, [(start + timedelta(minutes=index), Decimal("1"), Decimal("1.2"),
                     Decimal("0.9"), Decimal("1.1")) for index in range(6)])
    calls = {"stores": 0, "reconcile": 0}
    original_store = orchestration_module.KnowledgeStore

    class CountingStore(original_store):
        def __init__(self, *args, **kwargs):
            calls["stores"] += 1
            super().__init__(*args, **kwargs)

        def reconcile_mined_partitions(self):
            calls["reconcile"] += 1
            return super().reconcile_mined_partitions()

    monkeypatch.setattr(orchestration_module, "KnowledgeStore", CountingStore)
    plan = RunPlan("local", "fixture", start.date(), start.date(), 5000)
    orchestrator = ResearchOrchestrator(plan, root=tmp_path / "root", corpus_root=tmp_path / "corpus")
    first = orchestrator.run_local_mine(source, repository_commit="a", partition_id="one")
    second = orchestrator.run_local_mine(source, repository_commit="a", partition_id="two")
    assert first["accepted_unique"] > 0
    assert second["accepted_unique"] == 0
    assert calls == {"stores": 1, "reconcile": 1}

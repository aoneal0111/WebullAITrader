from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.catalysts.models import CatalystEvidence
from app.catalysts.sec_shadow_parity import (
    MismatchKind,
    ParityState,
    ReadinessReason,
    SecCatalystParityStore,
    SecCatalystShadowEvaluator,
    SecShadowMetrics,
    compare_sec_catalyst_evidence,
)
from app.momentum_scanner.models import CatalystStatus, CatalystType


NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def evidence(status=CatalystStatus.TRUE, *, accession="0000000001-01-000001", published=NOW,
             headline="SEC 8-K filing", url="https://www.sec.gov/Archives/edgar/data/1/x/doc.htm"):
    return CatalystEvidence(
        symbol="ABC", catalyst_type=(CatalystType.SEC_FILING if status is CatalystStatus.TRUE else CatalystType.NONE),
        status=status, headline=(headline if status is CatalystStatus.TRUE else None), source="SEC_EDGAR",
        published_at=(published if status is CatalystStatus.TRUE else None),
        source_url=(url if status is CatalystStatus.TRUE else None),
        provider_event_id=(accession if status is CatalystStatus.TRUE else None),
        canonical_event_id=(f"sec-filing:{accession.casefold()}" if status is CatalystStatus.TRUE else None),
    )


def test_comparator_exact_matches_and_false_negative():
    assert compare_sec_catalyst_evidence(symbol="ABC", observed_at=NOW, environment="PAPER",
        legacy=evidence(), shadow=evidence(), shadow_ready=True).state is ParityState.MATCH
    assert compare_sec_catalyst_evidence(symbol="ABC", observed_at=NOW, environment="PAPER",
        legacy=evidence(CatalystStatus.FALSE), shadow=evidence(CatalystStatus.FALSE), shadow_ready=True).state is ParityState.MATCH


def test_comparator_status_contradictions_are_critical():
    result = compare_sec_catalyst_evidence(symbol="ABC", observed_at=NOW, environment="PAPER",
        legacy=evidence(), shadow=evidence(CatalystStatus.FALSE), shadow_ready=True)
    assert result.state is ParityState.MISMATCH
    assert MismatchKind.CRITICAL_STATUS_CONTRADICTION in result.mismatch_kinds
    reverse = compare_sec_catalyst_evidence(symbol="ABC", observed_at=NOW, environment="PAPER",
        legacy=evidence(CatalystStatus.FALSE), shadow=evidence(), shadow_ready=True)
    assert MismatchKind.CRITICAL_STATUS_CONTRADICTION in reverse.mismatch_kinds


@pytest.mark.parametrize("field", ["accession", "published", "url", "headline"])
def test_comparator_true_field_mismatches(field):
    kwargs = {"accession": "0000000001-01-000002", "published": NOW + timedelta(seconds=1),
              "url": "https://www.sec.gov/other", "headline": "SEC S-1 filing"}
    legacy = evidence()
    shadow = evidence(**{field: kwargs[field]})
    result = compare_sec_catalyst_evidence(symbol="ABC", observed_at=NOW, environment="PAPER",
        legacy=legacy, shadow=shadow, shadow_ready=True)
    expected = {"accession": MismatchKind.EVENT_ID_MISMATCH, "published": MismatchKind.PUBLISHED_AT_MISMATCH,
                "url": MismatchKind.SOURCE_URL_MISMATCH, "headline": MismatchKind.HEADLINE_MISMATCH}[field]
    assert result.state is ParityState.MISMATCH and expected in result.mismatch_kinds


def test_not_ready_is_not_semantic_mismatch():
    result = compare_sec_catalyst_evidence(symbol="ABC", observed_at=NOW, environment="PAPER",
        legacy=evidence(), shadow=evidence(CatalystStatus.UNKNOWN), shadow_ready=False,
        readiness_reason=ReadinessReason.SOURCE_STATE_MISSING)
    assert result.state is ParityState.SHADOW_NOT_READY
    assert result.readiness_reason is ReadinessReason.SOURCE_STATE_MISSING


def test_evaluator_returns_exact_legacy_object_and_counts():
    class Adapter:
        def get_evidence(self, symbol, *, as_of):
            return evidence()
    metrics = SecShadowMetrics()
    evaluator = SecCatalystShadowEvaluator(Adapter(), metrics=metrics)
    legacy = evidence()
    assert evaluator.evaluate("ABC", as_of=NOW, legacy_evidence=legacy) is legacy
    assert metrics.snapshot().evaluations == 1
    assert metrics.snapshot().matches == 1


def test_evaluator_adapter_failure_isolated():
    class Broken:
        def get_evidence(self, symbol, *, as_of):
            raise RuntimeError("do not persist")
    metrics = SecShadowMetrics()
    legacy = evidence()
    assert SecCatalystShadowEvaluator(Broken(), metrics=metrics).evaluate("ABC", as_of=NOW, legacy_evidence=legacy) is legacy
    assert metrics.snapshot().shadow_errors == 1


def test_store_bounds_restart_and_summary(tmp_path):
    store = SecCatalystParityStore(str(tmp_path / "parity.sqlite3"), max_rows=5, max_rows_per_symbol=2, retention_days=30)
    for index in range(7):
        item = compare_sec_catalyst_evidence(symbol="ABC" if index < 4 else f"S{index}", observed_at=NOW + timedelta(seconds=index),
            environment="PAPER", legacy=evidence(), shadow=evidence(accession=f"0000000001-01-{index+1:06d}"), shadow_ready=True)
        store.record(item)
    assert store.summary(since=NOW - timedelta(days=1)).total <= 5
    assert store.summary(since=NOW - timedelta(days=1)).mismatches >= 1
    reopened = SecCatalystParityStore(str(tmp_path / "parity.sqlite3"), max_rows=5, max_rows_per_symbol=2, retention_days=30)
    summary = reopened.summary(since=NOW - timedelta(days=1), limit_examples=1)
    assert summary.eligible == summary.matches + summary.mismatches
    assert len(summary.examples) <= 1


def test_store_durable_dedupe_and_retention(tmp_path):
    store = SecCatalystParityStore(str(tmp_path / "parity.sqlite3"), max_rows=10, max_rows_per_symbol=10, retention_days=1)
    item = compare_sec_catalyst_evidence(symbol="ABC", observed_at=NOW, environment="PAPER", legacy=evidence(), shadow=evidence(), shadow_ready=True)
    assert store.record(item) is True
    assert store.record(item) is False
    old = compare_sec_catalyst_evidence(symbol="OLD", observed_at=NOW - timedelta(days=2), environment="PAPER", legacy=evidence(), shadow=evidence(), shadow_ready=True)
    store.record(old)
    fresh = store.summary(since=NOW - timedelta(days=3))
    assert all(row.observed_at >= NOW - timedelta(days=1) for row in fresh.examples)


def test_evaluator_memory_dedupe_bound_and_storage_failure(tmp_path):
    class Adapter:
        def get_evidence(self, symbol, *, as_of):
            return evidence()
    class BrokenStore:
        def record(self, observation):
            raise OSError("private")
    metrics = SecShadowMetrics()
    evaluator = SecCatalystShadowEvaluator(Adapter(), store=BrokenStore(), metrics=metrics, max_dedupe_keys=4)
    legacy = evidence()
    for index in range(6):
        evaluator.evaluate(f"S{index}", as_of=NOW, legacy_evidence=legacy)
    assert metrics.snapshot().storage_failures == 6
    assert len(evaluator._dedupe) <= 4


def test_evaluator_dedupe_suppresses_repeat_store_write(tmp_path):
    class Adapter:
        def get_evidence(self, symbol, *, as_of):
            return evidence()
    store = SecCatalystParityStore(str(tmp_path / "parity.sqlite3"))
    metrics = SecShadowMetrics()
    evaluator = SecCatalystShadowEvaluator(Adapter(), store=store, metrics=metrics)
    legacy = evidence()
    evaluator.evaluate("ABC", as_of=NOW, legacy_evidence=legacy)
    evaluator.evaluate("ABC", as_of=NOW, legacy_evidence=legacy)
    assert metrics.snapshot().evaluations == 2
    assert metrics.snapshot().deduplicated_observations == 1
    assert store.summary(since=NOW - timedelta(days=1)).total == 1


def test_concurrent_store_writers_are_safe(tmp_path):
    store = SecCatalystParityStore(str(tmp_path / "parity.sqlite3"), max_rows=100)
    items = [compare_sec_catalyst_evidence(symbol=f"S{i}", observed_at=NOW + timedelta(seconds=i), environment="PAPER",
        legacy=evidence(), shadow=evidence(accession=f"0000000001-01-{i+1:06d}"), shadow_ready=True) for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(store.record, items))
    assert store.summary(since=NOW - timedelta(days=1)).total == 20


def test_invalid_summary_example_limit(tmp_path):
    store = SecCatalystParityStore(str(tmp_path / "parity.sqlite3"))
    with pytest.raises(ValueError):
        store.summary(since=NOW, limit_examples=51)

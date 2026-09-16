from datetime import UTC, datetime, timedelta
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest

from app.catalysts.sec_symbol_intelligence_adapter import (
    AdapterReadiness,
    AdapterReadinessReason,
    SecSymbolIntelligenceCatalystAdapter,
)
from app.momentum_scanner.models import CatalystStatus
from app.symbol_intelligence import (
    DecayClass,
    EventType,
    IntelligenceEvent,
    SecIssuerAcquisitionStatus,
    SourceAvailability,
    SourceStateSnapshot,
    SymbolIntelligenceRepository,
)
from app.symbol_intelligence.providers.sec_identity import parse_sec_ticker_map


NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)
T = datetime(2026, 1, 1, 12, tzinfo=UTC)


def repository(tmp_path, *, source_observed_at=NOW):
    repo = SymbolIntelligenceRepository(tmp_path / "si.sqlite3")
    repo.apply_sec_ticker_map(parse_sec_ticker_map(
        {"0": {"ticker": "ABC", "cik_str": 123, "title": "Issuer"}},
        source="SEC_EDGAR", observed_at=source_observed_at,
    ))
    repo.store_source_state(SourceStateSnapshot("SEC_EDGAR", SourceAvailability.AVAILABLE, source_observed_at))
    return repo


def test_missing_and_in_progress_state_are_not_ready(tmp_path):
    repo = repository(tmp_path, source_observed_at=T)
    adapter = SecSymbolIntelligenceCatalystAdapter(repo)
    missing = adapter.evaluate("ABC", as_of=NOW)
    assert (missing.readiness, missing.reason) == (AdapterReadiness.NOT_READY, AdapterReadinessReason.ISSUER_NOT_ACQUIRED)
    repo.record_sec_issuer_acquisition_attempt("SEC_CIK:0000000123", NOW)
    incomplete = adapter.evaluate("ABC", as_of=NOW)
    assert (incomplete.readiness, incomplete.reason) == (AdapterReadiness.NOT_READY, AdapterReadinessReason.ISSUER_ACQUISITION_INCOMPLETE)


def test_complete_zero_fact_state_allows_healthy_false(tmp_path):
    repo = repository(tmp_path, source_observed_at=T)
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", NOW, next_due_at=NOW + timedelta(seconds=900))
    result = SecSymbolIntelligenceCatalystAdapter(repo).evaluate("ABC", as_of=NOW)
    assert result.readiness is AdapterReadiness.READY
    assert result.evidence.status is CatalystStatus.FALSE


def test_future_and_stale_completion_are_not_ready(tmp_path):
    repo = repository(tmp_path)
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", NOW + timedelta(minutes=1), next_due_at=NOW + timedelta(minutes=16))
    future = SecSymbolIntelligenceCatalystAdapter(repo).evaluate("ABC", as_of=NOW)
    assert future.reason is AdapterReadinessReason.ISSUER_ACQUISITION_INCOMPLETE
    stale_repo = repository(tmp_path / "stale")
    stale_repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", NOW, next_due_at=NOW)
    stale = SecSymbolIntelligenceCatalystAdapter(stale_repo).evaluate("ABC", as_of=NOW)
    assert stale.reason is AdapterReadinessReason.ISSUER_ACQUISITION_STALE


def test_exact_completion_and_expiry_boundaries_are_deterministic(tmp_path):
    repo = repository(tmp_path, source_observed_at=T)
    due = T + timedelta(seconds=900)
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", T, next_due_at=due)
    adapter = SecSymbolIntelligenceCatalystAdapter(repo, clock=lambda: datetime(2030, 1, 1, tzinfo=UTC))
    assert adapter.evaluate("ABC", as_of=T - timedelta(microseconds=1)).readiness is AdapterReadiness.NOT_READY
    assert adapter.evaluate("ABC", as_of=T).readiness is AdapterReadiness.READY
    assert adapter.evaluate("ABC", as_of=due - timedelta(microseconds=1)).readiness is AdapterReadiness.READY
    assert adapter.evaluate("ABC", as_of=due).reason is AdapterReadinessReason.ISSUER_ACQUISITION_STALE
    assert adapter.evaluate("ABC", as_of=due + timedelta(microseconds=1)).reason is AdapterReadinessReason.ISSUER_ACQUISITION_STALE


def test_historical_as_of_is_not_invalidated_by_current_clock(tmp_path):
    repo = repository(tmp_path, source_observed_at=T)
    due = T + timedelta(seconds=900)
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", T, next_due_at=due)
    adapter = SecSymbolIntelligenceCatalystAdapter(repo, clock=lambda: datetime(2030, 1, 1, tzinfo=UTC))
    assert adapter.evaluate("ABC", as_of=T + timedelta(minutes=5)).readiness is AdapterReadiness.READY
    assert adapter.evaluate("ABC", as_of=due - timedelta(microseconds=1)).readiness is AdapterReadiness.READY
    assert adapter.evaluate("ABC", as_of=due).reason is AdapterReadinessReason.ISSUER_ACQUISITION_STALE


def test_failed_attempt_preserves_prior_success(tmp_path):
    repo = repository(tmp_path)
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", NOW, next_due_at=NOW + timedelta(seconds=900))
    repo.record_sec_issuer_acquisition_attempt("SEC_CIK:0000000123", NOW + timedelta(minutes=1))
    state = repo.fail_sec_issuer_acquisition("SEC_CIK:0000000123", NOW + timedelta(minutes=1), failure_category="TRANSPORT")
    assert state.status is SecIssuerAcquisitionStatus.FAILED
    assert state.last_complete_observation_at == NOW
    reopened = SymbolIntelligenceRepository(repo.path)
    assert reopened.get_sec_issuer_acquisition_state("SEC_CIK:0000000123").last_complete_observation_at == NOW


def test_v3_migrates_idempotently_without_data_loss(tmp_path):
    repo = repository(tmp_path)
    with sqlite3.connect(repo.path) as connection:
        connection.execute("DROP TABLE sec_issuer_acquisition_state")
        connection.execute("UPDATE repository_metadata SET value='3' WHERE key='schema_version'")
    migrated = SymbolIntelligenceRepository(repo.path)
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute("SELECT value FROM repository_metadata WHERE key='schema_version'").fetchone()[0] == "4"
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='sec_issuer_acquisition_state'").fetchone()
    assert migrated.resolve_symbol_identity("ABC", NOW).identity is not None


def test_v3_migration_preserves_identity_alias_event_and_source_state(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "fixture.sqlite3")
    old_at = T
    new_at = T + timedelta(minutes=1)
    assert repo.apply_sec_ticker_map(parse_sec_ticker_map({"0": {"ticker": "OLD", "cik_str": 123}}, source="SEC_EDGAR", observed_at=old_at))
    assert repo.apply_sec_ticker_map(parse_sec_ticker_map({"0": {"ticker": "NEW", "cik_str": 123}}, source="SEC_EDGAR", observed_at=new_at))
    repo.store_source_state(SourceStateSnapshot("SEC_EDGAR", SourceAvailability.AVAILABLE, old_at))
    repo.append_evidence((IntelligenceEvent(
        event_id="SEC_EDGAR:fixture", symbol="OLD", issuer_id="SEC_CIK:0000000123",
        event_type=EventType.SEC_FILING, event_subtype="8-K", source="SEC_EDGAR", source_id="fixture",
        published_at=old_at, observed_at=old_at, verified=True, decay_class=DecayClass.MULTI_DAY,
        source_parser_version="test", metadata={"form": "8-K", "filing_date": "2026-01-01", "primary_document": "fixture.htm"},
    ),))
    with sqlite3.connect(repo.path) as connection:
        connection.execute("DROP TABLE sec_issuer_acquisition_state")
        connection.execute("UPDATE repository_metadata SET value='3' WHERE key='schema_version'")
    migrated = SymbolIntelligenceRepository(repo.path)
    assert migrated.resolve_symbol_identity("OLD", old_at).identity.issuer_id == "SEC_CIK:0000000123"
    assert migrated.resolve_symbol_identity("NEW", new_at).identity.issuer_id == "SEC_CIK:0000000123"
    assert len(migrated.recent_sec_events_by_issuer("SEC_CIK:0000000123", limit=10, fact_cutoff=new_at)) == 1
    assert migrated.get_source_state("SEC_EDGAR").availability is SourceAvailability.AVAILABLE
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM symbol_aliases").fetchone()[0] == 2


def test_failed_v3_migration_rolls_back_schema_and_metadata(tmp_path, monkeypatch):
    repo = repository(tmp_path / "failed.sqlite3")
    with sqlite3.connect(repo.path) as connection:
        connection.execute("DROP TABLE sec_issuer_acquisition_state")
        connection.execute("UPDATE repository_metadata SET value='3' WHERE key='schema_version'")
    import app.symbol_intelligence.repository as repository_module
    original = repository_module._migrate_v3_to_v4
    def fail(connection):
        connection.execute("CREATE TABLE partial_readiness_marker(value TEXT)")
        raise RuntimeError("injected migration failure")
    monkeypatch.setattr(repository_module, "_migrate_v3_to_v4", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            SymbolIntelligenceRepository(repo.path)
    finally:
        monkeypatch.setattr(repository_module, "_migrate_v3_to_v4", original)
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute("SELECT value FROM repository_metadata WHERE key='schema_version'").fetchone()[0] == "3"
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='sec_issuer_acquisition_state'").fetchone() is None
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='partial_readiness_marker'").fetchone() is None
    reopened = SymbolIntelligenceRepository(repo.path)
    assert reopened.get_sec_issuer_acquisition_state("SEC_CIK:0000000123") is None


def test_ticker_changes_share_one_readiness_row(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "aliases.sqlite3")
    assert repo.apply_sec_ticker_map(parse_sec_ticker_map({"0": {"ticker": "OLD", "cik_str": 123}}, source="SEC_EDGAR", observed_at=T))
    assert repo.apply_sec_ticker_map(parse_sec_ticker_map({"0": {"ticker": "NEW", "cik_str": 123}}, source="SEC_EDGAR", observed_at=T + timedelta(minutes=1)))
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", T + timedelta(minutes=2), next_due_at=T + timedelta(minutes=17))
    assert repo.resolve_symbol_identity("OLD", T).identity.issuer_id == repo.resolve_symbol_identity("NEW", T + timedelta(minutes=1)).identity.issuer_id
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sec_issuer_acquisition_state").fetchone()[0] == 1


def test_multiple_share_classes_share_one_readiness_row(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "shares.sqlite3")
    assert repo.apply_sec_ticker_map(parse_sec_ticker_map({
        "0": {"ticker": "ABC", "cik_str": 123, "share_class": "A"},
        "1": {"ticker": "ABC-B", "cik_str": 123, "share_class": "B"},
    }, source="SEC_EDGAR", observed_at=NOW))
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", NOW, next_due_at=NOW + timedelta(seconds=900))
    assert repo.resolve_symbol_identity("ABC", NOW).identity.issuer_id == repo.resolve_symbol_identity("ABC-B", NOW).identity.issuer_id
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sec_issuer_acquisition_state").fetchone()[0] == 1


def test_readiness_lookup_is_indexed_and_cross_thread_safe(tmp_path):
    repo = repository(tmp_path)
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", NOW, next_due_at=NOW + timedelta(seconds=900))
    with sqlite3.connect(repo.path) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM sec_issuer_acquisition_state WHERE issuer_id=? LIMIT 1",
            ("SEC_CIK:0000000123",),
        ).fetchone()[3]
    assert "USING INDEX" in plan.upper() or "USING INTEGER PRIMARY KEY" in plan.upper()
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(lambda _: repo.get_sec_issuer_acquisition_state("SEC_CIK:0000000123"), range(16)))
    assert all(state is not None and state.status is SecIssuerAcquisitionStatus.COMPLETE for state in states)


def test_deterministic_concurrent_read_write_sees_only_committed_states(tmp_path):
    repo = repository(tmp_path)
    repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", NOW, next_due_at=NOW + timedelta(seconds=900))
    later = NOW + timedelta(seconds=30)

    def round_trip(_):
        barrier = Barrier(2)
        def writer():
            barrier.wait()
            repo.complete_sec_issuer_acquisition("SEC_CIK:0000000123", later, next_due_at=later + timedelta(seconds=900))
        def reader():
            barrier.wait()
            return repo.get_sec_issuer_acquisition_state("SEC_CIK:0000000123")
        with ThreadPoolExecutor(max_workers=2) as pool:
            read = pool.submit(reader)
            pool.submit(writer).result()
            return read.result()

    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(round_trip, range(8)))
    assert all(state is not None for state in states)
    assert all(state.status is SecIssuerAcquisitionStatus.COMPLETE for state in states)
    assert all(state.last_complete_observation_at in (NOW, later) for state in states)
    assert all(state.next_due_at in (NOW + timedelta(seconds=900), later + timedelta(seconds=900)) for state in states)

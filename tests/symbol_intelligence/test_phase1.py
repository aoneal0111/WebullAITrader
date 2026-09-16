from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import inspect
import sqlite3

import pytest

from app.symbol_intelligence import (
    AttentionState,
    DecayClass,
    Direction,
    EventType,
    HotSnapshotCache,
    IntelligenceDerivation,
    IntelligenceEvent,
    RepositoryBounds,
    RepositorySchemaError,
    SymbolIntelligenceRepository,
    SymbolIntelligenceSnapshot,
    UnsafePayloadError,
)


NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


def event(
    identity: str = "event-1",
    *,
    symbol: str = "AUTO",
    issuer_id: str | None = None,
    source_id: str | None = "source-1",
    published_at: datetime = NOW,
    observed_at: datetime | None = None,
    metadata=None,
) -> IntelligenceEvent:
    return IntelligenceEvent(
        event_id=identity,
        symbol=symbol,
        event_type=EventType.SEC_FILING,
        event_subtype="8-K",
        source="SEC_EDGAR",
        source_id=source_id,
        issuer_id=issuer_id,
        published_at=published_at,
        observed_at=observed_at or published_at,
        verified=True,
        decay_class=DecayClass.MULTI_DAY,
        source_parser_version="sec-parser-v1",
        headline="SEC 8-K filing",
        source_reference="https://www.sec.gov/example?private=removed#fragment",
        metadata={} if metadata is None else metadata,
    )


def snapshot(
    symbol: str,
    *,
    at: datetime = NOW,
    state: AttentionState = AttentionState.WATCH,
    priority: int = 50,
) -> SymbolIntelligenceSnapshot:
    return SymbolIntelligenceSnapshot(
        symbol=symbol,
        as_of=at,
        generated_at=at,
        attention_state=state,
        priority_score=priority,
        priority_reasons=("material_event",),
        fact_cutoff=at,
        derivation_versions=("classifier-v1",),
        missing_fields=("runner_history",),
    )


def repository(tmp_path, *, bounds: RepositoryBounds | None = None):
    return SymbolIntelligenceRepository(
        tmp_path / "symbol-intelligence.sqlite3",
        bounds=bounds or RepositoryBounds(),
    )


def test_recent_sec_events_by_issuer_is_bounded_point_in_time_and_deterministic(tmp_path):
    repo = repository(tmp_path)
    issuer_a = "SEC_CIK:0000000123"
    cutoff = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    published = cutoff - timedelta(minutes=5)
    events = (
        event("old-symbol", symbol="OLD", issuer_id=issuer_a, source_id="acc-old", published_at=published),
        event("same-z", symbol="NEW", issuer_id=issuer_a, source_id="acc-z", published_at=cutoff),
        event("same-a", symbol="NEW", issuer_id=issuer_a, source_id="acc-a", published_at=cutoff),
        event("future-observed", issuer_id=issuer_a, source_id="acc-future-observed",
              published_at=cutoff - timedelta(minutes=1), observed_at=cutoff + timedelta(seconds=1)),
        event("future-published", issuer_id=issuer_a, source_id="acc-future-published",
              published_at=cutoff + timedelta(seconds=1)),
        event("other-issuer", issuer_id="SEC_CIK:0000000999", source_id="acc-other",
              published_at=cutoff),
    )
    # A non-SEC event for the same issuer must be excluded by the canonical filters.
    non_sec = IntelligenceEvent(
        event_id="news-1", symbol="NEW", issuer_id=issuer_a,
        event_type=EventType.MATERIAL_AGREEMENT, event_subtype="NEWS",
        source="NEWS", source_id="news-1", published_at=cutoff,
        observed_at=cutoff, verified=True, decay_class=DecayClass.MULTI_DAY,
        source_parser_version="test", headline="news",
    )
    assert repo.append_evidence(events + (non_sec,)).inserted == 7

    before = repo.path.stat().st_mtime_ns
    result = repo.recent_sec_events_by_issuer(issuer_a, limit=32, fact_cutoff=cutoff)
    assert repo.path.stat().st_mtime_ns == before
    assert [item.event_id for item in result] == ["same-z", "same-a", "old-symbol"]
    assert all(item.source == "SEC_EDGAR" and item.event_type is EventType.SEC_FILING for item in result)
    assert result[0].symbol == "NEW" and result[-1].symbol == "OLD"
    assert repo.recent_sec_events_by_issuer(issuer_a, limit=2, fact_cutoff=cutoff)
    assert [item.event_id for item in repo.recent_sec_events_by_issuer(issuer_a, limit=2, fact_cutoff=cutoff)] == ["same-z", "same-a"]

    reopened = SymbolIntelligenceRepository(repo.path)
    assert [item.event_id for item in reopened.recent_sec_events_by_issuer(issuer_a, limit=32, fact_cutoff=cutoff)] == ["same-z", "same-a", "old-symbol"]


@pytest.mark.parametrize("limit", [0, -1, True, 5_001])
def test_recent_sec_events_by_issuer_rejects_invalid_limits(tmp_path, limit):
    with pytest.raises(ValueError):
        repository(tmp_path).recent_sec_events_by_issuer(
            "SEC_CIK:0000000123", limit=limit,
            fact_cutoff=NOW,
        )


def test_recent_sec_events_by_issuer_requires_aware_cutoff_and_valid_issuer(tmp_path):
    repo = repository(tmp_path)
    with pytest.raises(ValueError):
        repo.recent_sec_events_by_issuer("", limit=1, fact_cutoff=NOW)
    with pytest.raises(ValueError):
        repo.recent_sec_events_by_issuer("SEC_CIK:1", limit=1, fact_cutoff=datetime(2026, 9, 15, 12))


def test_contracts_are_versioned_immutable_and_separate() -> None:
    fact = event(metadata={"form": "8-K", "item_codes": ["1.01"]})
    assert fact.fact_schema_version == 1
    assert fact.source_reference == "https://www.sec.gov/example"
    assert not hasattr(fact, "direction")
    with pytest.raises(FrozenInstanceError):
        fact.symbol = "OTHER"

    derived = IntelligenceDerivation(
        derivation_id="derived-1",
        symbol="AUTO",
        derivation_type="DILUTION_RISK",
        direction=Direction.NEGATIVE,
        significance=80,
        confidence=Decimal("0.75"),
        algorithm_id="filing-classifier",
        algorithm_version="1",
        generated_at=NOW,
        fact_cutoff=NOW,
        supporting_event_ids=(fact.event_id,),
    )
    assert derived.derivation_schema_version == 1
    assert derived.supporting_event_ids == (fact.event_id,)
    with pytest.raises(FrozenInstanceError):
        derived.significance = 1


def test_snapshot_is_immutable_and_has_no_economic_authority() -> None:
    value = snapshot("auto", state=AttentionState.ACTIVE_OPPORTUNITY, priority=100)
    names = {item.name for item in fields(SymbolIntelligenceSnapshot)}
    assert not names.intersection({
        "entry_authority", "entry_permission", "risk_authority", "risk_permission",
        "order_authority", "order_permission", "execution_authority",
        "execution_permission", "broker_authority", "position_mutation",
    })
    assert value.symbol == "AUTO"
    with pytest.raises(FrozenInstanceError):
        value.priority_score = 0
    from_list = SymbolIntelligenceSnapshot(
        symbol="AUTO", as_of=NOW, generated_at=NOW,
        active_catalysts=[], source_states=[],
    )
    assert from_list.active_catalysts == ()
    assert from_list.source_states == ()


@pytest.mark.parametrize("state", tuple(AttentionState))
def test_every_attention_state_is_observational_only(state: AttentionState) -> None:
    value = snapshot("AUTO", state=state)
    assert value.attention_state is state
    assert not any("authority" in item.name or "permission" in item.name for item in fields(value))


def test_schema_bootstrap_and_newer_schema_rejection(tmp_path) -> None:
    repo = repository(tmp_path)
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute(
            "SELECT value FROM repository_metadata WHERE key='schema_version'"
        ).fetchone()[0] == "3"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('symbol_snapshots')")}
        assert "ix_snapshot_recovery" in indexes

    incompatible = tmp_path / "newer.sqlite3"
    with sqlite3.connect(incompatible) as connection:
        connection.execute("CREATE TABLE repository_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO repository_metadata VALUES('schema_version','999')")
    with pytest.raises(RepositorySchemaError, match="newer"):
        SymbolIntelligenceRepository(incompatible)

    incomplete = tmp_path / "incomplete.sqlite3"
    with sqlite3.connect(incomplete) as connection:
        connection.execute("CREATE TABLE repository_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO repository_metadata VALUES('schema_version','1')")
    with pytest.raises(RepositorySchemaError, match="incomplete"):
        SymbolIntelligenceRepository(incomplete)


def test_schema_v2_migrates_issuer_query_index_without_data_loss(tmp_path) -> None:
    repo = repository(tmp_path)
    with sqlite3.connect(repo.path) as connection:
        connection.execute("DROP INDEX ix_raw_event_issuer_source_published")
        connection.execute("UPDATE repository_metadata SET value='2' WHERE key='schema_version'")
    migrated = SymbolIntelligenceRepository(repo.path)
    with sqlite3.connect(migrated.path) as connection:
        assert connection.execute(
            "SELECT value FROM repository_metadata WHERE key='schema_version'"
        ).fetchone()[0] == "3"
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('raw_events')")}
        assert "ix_raw_event_issuer_source_published" in indexes


def test_connection_configuration_is_applied_per_operation(tmp_path) -> None:
    repo = repository(tmp_path)
    with repo._operation_connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].casefold() == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 2_000


def test_event_deduplication_by_source_identity_and_caller_identity(tmp_path) -> None:
    repo = repository(tmp_path)
    first = repo.append_evidence((event(),))
    source_duplicate = repo.append_evidence((event("different-event-id"),))
    id_duplicate = repo.append_evidence((event(source_id=None),))
    assert (first.inserted, first.deduplicated, first.rejected) == (1, 0, 0)
    assert (source_duplicate.inserted, source_duplicate.deduplicated) == (0, 1)
    assert (id_duplicate.inserted, id_duplicate.deduplicated) == (0, 1)
    assert repo.metrics().events_deduplicated == 2


def test_bounds_reject_without_unbounded_growth(tmp_path) -> None:
    repo = repository(tmp_path, bounds=RepositoryBounds(
        raw_events=2, active_event_states=2, episode_summaries=2,
        snapshots=2, derivations=2,
    ))
    result = repo.append_evidence(tuple(
        event(f"event-{index}", source_id=f"source-{index}") for index in range(3)
    ))
    assert (result.inserted, result.rejected) == (2, 1)
    assert dict(result.rejection_reasons) == {"RAW_EVENT_BOUND_REACHED": 1}
    assert dict(repo.metrics().table_row_counts)["raw_events"] == 2


def test_write_batches_are_hard_bounded_before_persistence(tmp_path) -> None:
    repo = repository(tmp_path)
    values = (
        event(f"event-{index}", source_id=f"source-{index}")
        for index in range(1_001)
    )
    with pytest.raises(ValueError, match="write batch"):
        repo.append_evidence(values)
    assert dict(repo.metrics().table_row_counts)["raw_events"] == 0


def test_derivation_requires_point_in_time_support_and_does_not_mutate_fact(tmp_path) -> None:
    repo = repository(tmp_path)
    fact = event()
    assert repo.append_evidence((fact,)).inserted == 1
    invalid = IntelligenceDerivation(
        derivation_id="future-derived", symbol="AUTO", derivation_type="RISK",
        direction=Direction.NEGATIVE, significance=60, confidence=Decimal("0.5"),
        algorithm_id="algorithm", algorithm_version="1",
        generated_at=NOW + timedelta(hours=1), fact_cutoff=NOW - timedelta(seconds=1),
        supporting_event_ids=(fact.event_id,),
    )
    valid = IntelligenceDerivation(
        derivation_id="valid-derived", symbol="AUTO", derivation_type="RISK",
        direction=Direction.NEGATIVE, significance=60, confidence=Decimal("0.5"),
        algorithm_id="algorithm", algorithm_version="1",
        generated_at=NOW + timedelta(hours=1), fact_cutoff=NOW,
        supporting_event_ids=(fact.event_id,),
    )
    assert repo.upsert_derivations((invalid,)).rejected == 1
    assert repo.upsert_derivations((valid,)).inserted == 1
    restored = repo.recent_events("AUTO", limit=1)[0]
    assert restored == fact
    assert restored.event_type is EventType.SEC_FILING


def test_point_in_time_snapshot_prefix_invariance(tmp_path) -> None:
    repo = repository(tmp_path)
    earlier = snapshot("AUTO", at=NOW, priority=30)
    later = snapshot("AUTO", at=NOW + timedelta(hours=2), priority=95)
    assert repo.store_snapshot(earlier)
    before = repo.snapshot_at("AUTO", NOW)
    assert repo.store_snapshot(later)
    assert repo.snapshot_at("AUTO", NOW) == before == earlier
    assert repo.snapshot_at("AUTO", NOW + timedelta(hours=2)) == later


def test_recent_events_is_symbol_scoped_time_bounded_and_limit_required(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.append_evidence(tuple(
        event(f"event-{index}", source_id=f"source-{index}", published_at=NOW + timedelta(minutes=index))
        for index in range(5)
    ))
    assert len(repo.recent_events("AUTO", limit=2)) == 2
    assert len(repo.recent_events("AUTO", limit=10, until=NOW + timedelta(minutes=1))) == 2
    with pytest.raises(TypeError):
        repo.recent_events("AUTO")
    with pytest.raises(ValueError):
        repo.recent_events("AUTO", limit=5_001)
    assert not hasattr(repo, "all_events")
    assert not hasattr(repo, "all_snapshots")
    assert not hasattr(repo, "records")


def test_hot_cache_is_bounded_priority_aware_and_io_free() -> None:
    cache = HotSnapshotCache(capacity=2, clock=lambda: NOW)
    active = snapshot("ACTIVE", state=AttentionState.ACTIVE_OPPORTUNITY, priority=90)
    background = snapshot("BACKGROUND", state=AttentionState.BACKGROUND, priority=10)
    elevated = snapshot("ELEVATED", state=AttentionState.ELEVATED, priority=70)
    assert cache.put(active)
    assert cache.put(background)
    assert cache.put(elevated)
    assert len(cache) == 2
    assert cache.hot_snapshot("ACTIVE") == active
    missing = cache.hot_snapshot("BACKGROUND")
    assert missing.unknown and missing.stale
    assert missing.missing_fields == ("all_intelligence",)
    assert "repository" not in inspect.signature(HotSnapshotCache).parameters
    assert "repository" not in vars(cache)
    assert cache.metrics().evictions == 1


def test_equal_priority_active_cache_rejects_incoming_without_age_eviction() -> None:
    cache = HotSnapshotCache(capacity=2, clock=lambda: NOW)
    oldest = snapshot(
        "OLDEST", at=NOW, state=AttentionState.ACTIVE_OPPORTUNITY, priority=80,
    )
    newer = snapshot(
        "NEWER", at=NOW + timedelta(minutes=1),
        state=AttentionState.ACTIVE_OPPORTUNITY, priority=80,
    )
    incoming = snapshot(
        "INCOMING", at=NOW + timedelta(minutes=2),
        state=AttentionState.ACTIVE_OPPORTUNITY, priority=80,
    )
    assert cache.put(oldest)
    assert cache.put(newer)

    assert not cache.put(incoming)
    assert len(cache) == 2
    assert cache.hot_snapshot("OLDEST") == oldest
    assert cache.hot_snapshot("NEWER") == newer
    assert cache.hot_snapshot("INCOMING").unknown
    assert cache.metrics().rejected_puts == 1
    assert cache.metrics().evictions == 0


def test_higher_priority_active_evicts_strictly_lower_priority_active() -> None:
    cache = HotSnapshotCache(capacity=2, clock=lambda: NOW)
    lower = snapshot("LOWER", state=AttentionState.ACTIVE_OPPORTUNITY, priority=70)
    higher = snapshot("HIGHER", state=AttentionState.ACTIVE_OPPORTUNITY, priority=80)
    incoming = snapshot("INCOMING", state=AttentionState.ACTIVE_OPPORTUNITY, priority=90)
    assert cache.put(lower)
    assert cache.put(higher)

    assert cache.put(incoming)
    assert len(cache) == 2
    assert cache.hot_snapshot("LOWER").unknown
    assert cache.hot_snapshot("HIGHER") == higher
    assert cache.hot_snapshot("INCOMING") == incoming


def test_active_incoming_prefers_non_active_victim() -> None:
    cache = HotSnapshotCache(capacity=2, clock=lambda: NOW)
    active = snapshot("ACTIVE", state=AttentionState.ACTIVE_OPPORTUNITY, priority=10)
    non_active = snapshot("WATCH", state=AttentionState.WATCH, priority=100)
    incoming = snapshot("INCOMING", state=AttentionState.ACTIVE_OPPORTUNITY, priority=10)
    assert cache.put(active)
    assert cache.put(non_active)

    assert cache.put(incoming)
    assert len(cache) == 2
    assert cache.hot_snapshot("ACTIVE") == active
    assert cache.hot_snapshot("WATCH").unknown
    assert cache.hot_snapshot("INCOMING") == incoming


def test_same_symbol_active_update_does_not_grow_cache() -> None:
    cache = HotSnapshotCache(capacity=1, clock=lambda: NOW)
    original = snapshot("ACTIVE", state=AttentionState.ACTIVE_OPPORTUNITY, priority=50)
    updated = snapshot(
        "ACTIVE", at=NOW + timedelta(minutes=1),
        state=AttentionState.ACTIVE_OPPORTUNITY, priority=60,
    )
    assert cache.put(original)

    assert cache.put(updated)
    assert len(cache) == 1
    assert cache.hot_snapshot("ACTIVE") == updated
    assert cache.metrics().evictions == 0


def test_lower_state_incoming_is_rejected_when_all_entries_are_active() -> None:
    cache = HotSnapshotCache(capacity=2, clock=lambda: NOW)
    first = snapshot("FIRST", state=AttentionState.ACTIVE_OPPORTUNITY, priority=10)
    second = snapshot("SECOND", state=AttentionState.ACTIVE_OPPORTUNITY, priority=20)
    incoming = snapshot("INCOMING", state=AttentionState.HIGH_PRIORITY, priority=100)
    assert cache.put(first)
    assert cache.put(second)

    assert not cache.put(incoming)
    assert len(cache) == 2
    assert cache.hot_snapshot("FIRST") == first
    assert cache.hot_snapshot("SECOND") == second
    assert cache.hot_snapshot("INCOMING").unknown
    assert cache.metrics().rejected_puts == 1


def test_recovery_is_indexed_hard_limited_and_priority_ordered(tmp_path) -> None:
    repo = repository(tmp_path)
    for index in range(10):
        repo.store_snapshot(snapshot(
            f"S{index}", priority=index,
            state=AttentionState.HIGH_PRIORITY if index >= 7 else AttentionState.WATCH,
        ))
    recovered = repo.recover_hot_state(limit=3)
    assert len(recovered) == 3
    assert [item.symbol for item in recovered] == ["S9", "S8", "S7"]
    assert "LIMIT ?" in inspect.getsource(SymbolIntelligenceRepository.recover_hot_state)
    with pytest.raises(ValueError):
        repo.recover_hot_state(limit=4_097)
    assert repo.metrics().recovery_rows == 3


@pytest.mark.parametrize("forbidden", (
    "api_key", "ApiKey", "token", "secret", "password", "credential",
    "authorization", "account_id", "user_agent",
))
def test_secret_keys_cannot_enter_event_metadata(forbidden: str) -> None:
    with pytest.raises(UnsafePayloadError):
        event(metadata={"details": {forbidden: "private"}})


def test_secrets_cannot_enter_derivation_snapshot_or_repository_metadata(tmp_path) -> None:
    with pytest.raises(UnsafePayloadError):
        IntelligenceDerivation(
            derivation_id="derived", symbol="AUTO", derivation_type="RISK",
            direction=Direction.UNKNOWN, significance=1, confidence=Decimal("0.1"),
            algorithm_id="algorithm", algorithm_version="1", generated_at=NOW,
            fact_cutoff=NOW, supporting_event_ids=("event-1",),
            metadata={"basis": {"Authorization": "private"}},
        )
    with pytest.raises(UnsafePayloadError):
        SymbolIntelligenceSnapshot(
            symbol="AUTO", as_of=NOW, generated_at=NOW,
            priority_reasons=("api_key leaked",),
        )
    repo = repository(tmp_path)
    with pytest.raises(UnsafePayloadError):
        repo.set_repository_metadata("SEC_USER_AGENT", "private")
    with pytest.raises(UnsafePayloadError):
        repo.store_episode_summary(
            episode_id="episode", symbol="AUTO", episode_type="RUNNER",
            started_at=NOW, ended_at=None,
            summary={"nested": {"password": "private"}},
        )


def test_repository_is_usable_across_threads_with_per_operation_connections(tmp_path) -> None:
    repo = repository(tmp_path)

    def write(index: int) -> tuple[int, bool]:
        result = repo.append_evidence((
            event(f"thread-event-{index}", symbol=f"T{index}", source_id=f"thread-source-{index}"),
        ))
        stored = repo.store_snapshot(snapshot(f"T{index}", priority=index))
        return result.inserted, stored

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(write, range(12)))
    assert results == ((1, True),) * 12
    assert len(repo.recover_hot_state(limit=12)) == 12
    assert dict(repo.metrics().table_row_counts)["raw_events"] == 12


def test_production_path_contract_is_environment_scoped() -> None:
    assert SymbolIntelligenceRepository.production_path("PAPER").as_posix() == (
        "data/symbol_intelligence/paper/symbol_intelligence.sqlite3"
    )

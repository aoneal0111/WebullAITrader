from datetime import UTC, datetime, timedelta
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.symbol_intelligence import SymbolAlias, SymbolIdentity, SymbolIntelligenceRepository
from app.symbol_intelligence.providers.sec_identity import (
    AmbiguousTickerMapError,
    SecIssuerIdentity,
    SecResolutionStatus,
    SecIssuerIdentityResolver,
    SecTickerMapError,
    normalize_sec_symbol,
    parse_sec_ticker_map,
    sec_cik_path,
    sec_issuer_id,
)


NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


def payload(rows):
    return {str(index): row for index, row in enumerate(rows)}


def ticker(ticker_name, cik, **extra):
    return {"ticker": ticker_name, "cik_str": cik, "title": extra.pop("title", "Issuer"), **extra}


def parsed(rows, *, at=NOW, source="SEC_EDGAR_TICKER_MAP"):
    return parse_sec_ticker_map(payload(rows), source=source, observed_at=at)


def test_contract_normalization_and_cik_formatting():
    assert normalize_sec_symbol(" brk.b ") == "BRK-B"
    assert normalize_sec_symbol("GOOG") != normalize_sec_symbol("GOOGL")
    assert sec_issuer_id(123456) == "SEC_CIK:0000123456"
    assert sec_cik_path(123456) == "CIK0000123456.json"


@pytest.mark.parametrize("value", [None, 0, -1, True, 10_000_000_000, "abc", 1.5])
def test_invalid_cik_rejected(value):
    with pytest.raises(SecTickerMapError):
        parsed([ticker("ABC", value)])


def test_exchange_map_shape_and_optional_fields():
    result = parse_sec_ticker_map(
        {"data": [ticker("ABC", "123", exchange="NASDAQ", share_class="Class A")]},
        source="SEC_EXCHANGE_MAP", observed_at=NOW,
    )
    identity = result.identities[0]
    assert identity.exchange == "NASDAQ"
    assert identity.share_class == "Class A"
    assert identity.issuer_id == "SEC_CIK:0000000123"


def test_collision_is_fail_closed():
    with pytest.raises(AmbiguousTickerMapError):
        parsed([ticker("BRK.B", 1), ticker("BRK-B", 2)])


def test_revision_is_order_invariant_and_changes_on_content():
    first = parsed([ticker("AAA", 1), ticker("BBB", 2)])
    reordered = parsed([ticker("BBB", 2), ticker("AAA", 1)])
    changed = parsed([ticker("AAA", 1), ticker("BBB", 3)])
    assert first.source_revision == reordered.source_revision
    assert first.source_revision != changed.source_revision


def test_byte_and_row_bounds_reject_complete_payload():
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map(json.dumps(payload([ticker("AAA", 1)])), source="S", observed_at=NOW,
                             max_entries=0)
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map("x" * (16 * 1024 * 1024 + 1), source="S", observed_at=NOW)


def test_atomic_apply_ticker_change_and_point_in_time(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    day1 = parsed([ticker("AAA", 1)], at=NOW)
    day2 = parsed([ticker("BBB", 1)], at=NOW + timedelta(days=1))
    assert repo.apply_sec_ticker_map(day1)
    assert repo.apply_sec_ticker_map(day2)
    old = repo.resolve_symbol_identity("AAA", NOW + timedelta(hours=1))
    current = repo.resolve_symbol_identity("AAA", NOW + timedelta(days=2))
    new = repo.resolve_symbol_identity("BBB", NOW + timedelta(days=2))
    assert old.status is SecResolutionStatus.RESOLVED and old.identity.cik == 1
    assert current.status is SecResolutionStatus.UNRESOLVED
    assert new.status is SecResolutionStatus.RESOLVED and new.identity.cik == 1


def test_symbol_reuse_and_unchanged_revision_no_churn(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    first = parsed([ticker("ABC", 1)], at=NOW)
    second = parsed([ticker("ABC", 2)], at=NOW + timedelta(days=1))
    assert repo.apply_sec_ticker_map(first)
    assert repo.apply_sec_ticker_map(second)
    before = repo.metrics().table_row_counts
    assert repo.apply_sec_ticker_map(second)
    after = repo.metrics().table_row_counts
    assert dict(before)["symbol_aliases"] == dict(after)["symbol_aliases"] == 2
    assert repo.resolve_symbol_identity("ABC", NOW + timedelta(hours=1)).identity.cik == 1
    assert repo.resolve_symbol_identity("ABC", NOW + timedelta(days=2)).identity.cik == 2


def test_invalid_replacement_leaves_previous_state(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    valid = parsed([ticker("AAA", 1)])
    assert repo.apply_sec_ticker_map(valid)
    with pytest.raises(AmbiguousTickerMapError):
        parsed([ticker("AAA", 1), ticker("AAA", 2)])
    assert repo.resolve_symbol_identity("AAA", NOW + timedelta(days=1)).identity.cik == 1


def test_stale_observation_is_rejected_without_rewriting_current_state(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    t1, t2, stale = NOW, NOW + timedelta(days=1), NOW + timedelta(hours=12)
    first, second, old = parsed([ticker("AAA", 1)], at=t1), parsed([ticker("AAA", 2)], at=t2), parsed([ticker("AAA", 3)], at=stale)
    assert repo.apply_sec_ticker_map(first) and repo.apply_sec_ticker_map(second)
    assert not repo.apply_sec_ticker_map(old)
    assert repo.resolve_symbol_identity("AAA", t2 + timedelta(minutes=1)).identity.cik == 2
    assert repo.resolve_symbol_identity("AAA", t1 + timedelta(hours=1)).identity.cik == 1
    with repo._operation_connection() as connection:
        values = dict(connection.execute("SELECT key,value FROM repository_metadata").fetchall())
    assert values["sec_ticker_map_revision"] == second.source_revision
    assert values["sec_ticker_map_last_observed"] == t2.isoformat()


def test_same_timestamp_conflict_rejected_and_same_revision_is_idempotent(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    first = parsed([ticker("ABC", 1)], at=NOW)
    conflict = parsed([ticker("ABC", 2)], at=NOW)
    assert repo.apply_sec_ticker_map(first)
    assert repo.apply_sec_ticker_map(first)
    assert not repo.apply_sec_ticker_map(conflict)
    assert repo.resolve_symbol_identity("ABC", NOW + timedelta(minutes=1)).identity.cik == 1
    with repo._operation_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM symbol_aliases").fetchone()[0] == 1


def test_concurrent_map_writers_cannot_commit_stale_or_conflicting_state(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    t = NOW + timedelta(days=2)
    maps = (parsed([ticker("CON", 1)], at=t), parsed([ticker("CON", 2)], at=t))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(repo.apply_sec_ticker_map, maps))
    assert sorted(results) == [False, True]
    assert repo.resolve_symbol_identity("CON", t + timedelta(minutes=1)).identity.cik in {1, 2}


def test_mid_transaction_failure_rolls_back_all_identity_alias_and_revision_changes(tmp_path, monkeypatch):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    old = parsed([ticker("OLD", 1)], at=NOW)
    new = parsed([ticker("NEW", 2)], at=NOW + timedelta(days=1))
    assert repo.apply_sec_ticker_map(old)
    def fail(_identity):
        raise RuntimeError("injected identity mutation failure")
    monkeypatch.setattr(repo, "_after_identity_mutation", fail)
    with pytest.raises(RuntimeError):
        repo.apply_sec_ticker_map(new)
    assert repo.resolve_symbol_identity("OLD", NOW + timedelta(hours=1)).identity.cik == 1
    assert repo.resolve_symbol_identity("NEW", NOW + timedelta(days=2)).status is SecResolutionStatus.UNRESOLVED
    with repo._operation_connection() as connection:
        values = dict(connection.execute("SELECT key,value FROM repository_metadata").fetchall())
        assert values["sec_ticker_map_revision"] == old.source_revision
        assert values["sec_ticker_map_last_observed"] == NOW.isoformat()
    monkeypatch.setattr(repo, "_after_identity_mutation", lambda _identity: None)
    assert repo.apply_sec_ticker_map(new)


def test_overlapping_historical_aliases_fail_closed(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    first = parsed([ticker("OVR", 1)], at=NOW)
    second = parsed([ticker("OTHER", 2)], at=NOW + timedelta(minutes=1))
    repo.apply_sec_ticker_map(first)
    repo.apply_sec_ticker_map(second)
    with repo._operation_connection() as connection:
        connection.execute("UPDATE symbol_aliases SET valid_to=? WHERE symbol=? AND valid_to IS NOT NULL", ((NOW + timedelta(hours=3)).isoformat(), "OVR"))
    repo.upsert_symbol_alias(SymbolAlias("OVR", "SEC_CIK:0000000002", NOW + timedelta(hours=1), NOW + timedelta(hours=3)))
    result = repo.resolve_symbol_identity("OVR", NOW + timedelta(hours=1, minutes=30))
    assert result.status is SecResolutionStatus.AMBIGUOUS


def test_restart_recovers_current_identity(tmp_path):
    path = tmp_path / "identity.sqlite3"
    repo = SymbolIntelligenceRepository(path)
    assert repo.apply_sec_ticker_map(parsed([ticker("AAA", 1), ticker("BBB", 2)]))
    restarted = SymbolIntelligenceRepository(path)
    assert restarted.current_symbol_identity("AAA").status is SecResolutionStatus.RESOLVED
    assert restarted.current_symbol_identity("BBB").identity.cik == 2


def test_bounded_current_resolver_recovery(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "identity.sqlite3")
    assert repo.apply_sec_ticker_map(parsed([ticker("AAA", 1), ticker("BBB", 2)]))
    resolver = SecIssuerIdentityResolver(repo, max_entries=2)
    assert resolver.recover() == 2
    assert resolver.resolve_current("AAA").identity.cik == 1
    assert resolver.size == 2


def test_resolver_snapshot_replacement_is_atomic_under_concurrent_reads():
    class Repo:
        def __init__(self):
            self.identities = ()
            self.barrier = Barrier(2)
            self.calls = 0
        def recover_current_identities(self, *, limit):
            values = self.identities
            self.calls += 1
            if self.calls > 1:
                self.barrier.wait()
            return values

    repo = Repo()
    first = SecIssuerIdentity("AAA", 101, sec_issuer_id(101), "AAA", "Issuer", None, None, "r1", NOW, True)
    second = SecIssuerIdentity("BBB", 202, sec_issuer_id(202), "BBB", "Issuer", None, None, "r2", NOW, True)
    repo.identities = (first,)
    resolver = SecIssuerIdentityResolver(repo)
    resolver.recover()
    repo.identities = (second,)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(resolver.recover)
        repo.barrier.wait()
        observed = resolver.resolve_current("AAA")
        future.result()
    assert observed.identity in (first, None)
    assert resolver.resolve_current("BBB").identity == second


def test_resolver_recovery_failure_preserves_last_snapshot():
    class Repo:
        def __init__(self, value): self.value = value
        def recover_current_identities(self, *, limit):
            if isinstance(self.value, Exception): raise self.value
            return self.value
    first = SecIssuerIdentity("AAA", 101, sec_issuer_id(101), "AAA", "Issuer", None, None, "r1", NOW, True)
    repo = Repo((first,))
    resolver = SecIssuerIdentityResolver(repo)
    resolver.recover()
    repo.value = RuntimeError("injected")
    with pytest.raises(RuntimeError): resolver.recover()
    assert resolver.resolve_current("AAA").identity == first

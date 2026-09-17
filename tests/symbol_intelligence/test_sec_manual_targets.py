from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from threading import Barrier, Event
from types import SimpleNamespace

from app.symbol_intelligence.composition import SymbolIntelligenceComposition
from app.symbol_intelligence.models import SecManualTargetStatus
from app.symbol_intelligence.providers.sec_identity import (
    SecIssuerIdentity,
    SecIssuerIdentityResolver,
    SecIssuerResolution,
    SecResolutionStatus,
    sec_issuer_id,
)


NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def identity(symbol: str, cik: int) -> SecIssuerIdentity:
    return SecIssuerIdentity(symbol, cik, sec_issuer_id(cik), symbol, "Issuer", None, None, "rev", NOW, True)


class FakeResolver:
    def __init__(self, identities=()):
        self.identities = {item.normalized_symbol: item for item in identities}
        self.calls = 0

    def resolve_current(self, symbol):
        self.calls += 1
        item = self.identities.get(symbol)
        if item is None:
            return SecIssuerResolution(symbol, symbol, SecResolutionStatus.UNRESOLVED, reason="CACHE_MISS")
        return SecIssuerResolution(symbol, symbol, SecResolutionStatus.RESOLVED, identity=item)


class FakeService:
    def __init__(self, *, ready=True, accepted=True):
        self.diagnostics = SimpleNamespace(ticker_map_ready=ready, ticker_map_last_success_at=NOW)
        self.accepted = accepted
        self.calls = []

    def enqueue_issuer(self, identity, priority):
        self.calls.append((identity, priority))
        return self.accepted


def composition(identities=(), *, ready=True, admission=True, accepted=True):
    service = FakeService(ready=ready, accepted=accepted)
    ownership = SimpleNamespace(admission_open=admission)
    result = SymbolIntelligenceComposition(
        object(), FakeResolver(identities), object(), object(), object(), service, ownership,
    )
    return result, service


def test_pre_ticker_target_is_not_ready_without_queue_mutation():
    target, service = composition([identity("AAA", 1)], ready=False)
    result = target.enqueue_symbols(["AAA"])
    assert result.not_ready == 1 and result.accepted == 0
    assert not service.calls


def test_three_issuers_and_cumulative_fourth_are_bounded():
    identities = [identity("AAA", 1), identity("BBB", 2), identity("CCC", 3), identity("DDD", 4)]
    target, service = composition(identities)
    assert target.enqueue_symbols(["AAA"]).accepted == 1
    assert target.enqueue_symbols(["BBB"]).accepted == 1
    assert target.enqueue_symbols(["CCC"]).accepted == 1
    fourth = target.enqueue_symbols(["DDD"])
    assert fourth.rejected_limit == 1
    assert len(service.calls) == 3
    assert target.target_diagnostics.target_unique_issuers_admitted == 3


def test_four_in_one_is_rejected_before_resolution_or_queue():
    target, service = composition([identity("AAA", 1)])
    before = target.target_diagnostics.target_unique_issuers_admitted
    result = target.enqueue_symbols(["AAA", "BBB", "CCC", "DDD"])
    assert result.accepted == 0 and result.rejected_limit == 4
    assert not service.calls
    assert target.target_diagnostics.target_unique_issuers_admitted == before


def test_duplicate_share_aliases_and_invalid_entries_are_bounded():
    issuer = identity("AAA", 1)
    target, service = composition([issuer])
    result = target.enqueue_symbols(["AAA", "AAA", "BAD!"])
    assert result.accepted == 1
    assert result.deduplicated == 1
    assert result.invalid == 1
    assert len(service.calls) == 1
    assert tuple(entry.status for entry in result.entries) == (
        SecManualTargetStatus.ACCEPTED, SecManualTargetStatus.DEDUPLICATED, SecManualTargetStatus.INVALID,
    )


def test_queue_rejection_does_not_consume_session_slot():
    target, service = composition([identity("AAA", 1)], accepted=False)
    assert target.enqueue_symbols(["AAA"]).queue_rejected == 1
    assert target.target_diagnostics.target_unique_issuers_admitted == 0


def test_queue_rejection_leaves_capacity_for_later_success():
    target, service = composition([identity("AAA", 1), identity("BBB", 2)], accepted=False)
    assert target.enqueue_symbols(["AAA"]).queue_rejected == 1
    service.accepted = True
    assert target.enqueue_symbols(["BBB"]).accepted == 1
    assert target.target_diagnostics.target_unique_issuers_admitted == 1


def test_ambiguous_identity_is_not_enqueued_or_counted():
    target, service = composition([identity("AAA", 1)])
    target.resolver.resolve_current = lambda symbol: SecIssuerResolution(
        symbol, symbol, SecResolutionStatus.AMBIGUOUS, reason="AMBIGUOUS"
    )
    result = target.enqueue_symbols(["AAA"])
    assert result.ambiguous == 1 and not service.calls
    assert target.target_diagnostics.target_unique_issuers_admitted == 0


def test_lifecycle_gate_rejects_without_resolution_or_queue():
    target, service = composition([identity("AAA", 1)], admission=False)
    result = target.enqueue_symbols(["AAA"])
    assert result.service_unavailable == 1
    assert not service.calls


def test_concurrent_final_slot_admission_is_race_safe():
    identities = [identity("AAA", 1), identity("BBB", 2), identity("CCC", 3), identity("DDD", 4)]
    target, service = composition(identities)
    assert target.enqueue_symbols(["AAA", "BBB"]).accepted == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(target.enqueue_symbols, (("CCC",), ("DDD",))))
    assert sum(item.accepted for item in results) == 1
    assert sum(item.rejected_limit for item in results) == 1
    assert target.target_diagnostics.target_unique_issuers_admitted == 3
    assert len(service.calls) == 3


def test_partial_validity_accepts_only_resolved_entry():
    target, service = composition([identity("AAA", 1)])
    result = target.enqueue_symbols(["AAA", "BAD!", "ZZZZ"])
    assert (result.accepted, result.invalid, result.unresolved) == (1, 1, 1)
    assert len(service.calls) == 1
    assert target.target_diagnostics.target_unique_issuers_admitted == 1
    assert tuple(entry.status for entry in result.entries) == (
        SecManualTargetStatus.ACCEPTED,
        SecManualTargetStatus.INVALID,
        SecManualTargetStatus.UNRESOLVED,
    )


def test_distinct_share_class_symbols_same_cik_dedupe():
    issuer_a = identity("ABC", 101)
    issuer_class = identity("ABC-A", 101)
    target, service = composition([issuer_a, issuer_class])
    result = target.enqueue_symbols(["ABC", "ABC.A"])
    assert result.accepted == 1 and result.deduplicated == 1
    assert len(service.calls) == 1
    assert target.target_diagnostics.target_unique_issuers_admitted == 1


def test_ticker_change_aliases_same_cik_dedupe():
    old = identity("OLD", 101)
    new = identity("NEW", 101)
    target, service = composition([old, new])
    result = target.enqueue_symbols(["OLD", "NEW"])
    assert result.accepted == 1 and result.deduplicated == 1
    assert len(service.calls) == 1
    assert target.target_diagnostics.target_unique_issuers_admitted == 1


def test_concurrent_duplicate_issuer_consumes_one_slot():
    target, service = composition([identity("AAA", 1)])
    barrier = Barrier(2)
    original = target.resolver.resolve_current

    def resolve(symbol):
        barrier.wait()
        return original(symbol)

    target.resolver.resolve_current = resolve
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(target.enqueue_symbols, (("AAA",), ("AAA",))))
    assert sum(item.accepted for item in results) == 1
    assert sum(item.deduplicated for item in results) == 1
    assert target.target_diagnostics.target_unique_issuers_admitted == 1
    assert len(service.calls) == 1


def test_target_call_during_real_resolver_snapshot_swap_sees_complete_snapshot():
    old = identity("AAA", 1)
    new = identity("AAA", 2)

    class Repo:
        def __init__(self):
            self.values = (old,)
            self.block = Event()
            self.release = Event()
            self.calls = 0
        def recover_current_identities(self, *, limit):
            self.calls += 1
            values = self.values
            if self.calls > 1:
                self.block.set()
                self.release.wait(2)
            return values

    repo = Repo()
    resolver = SecIssuerIdentityResolver(repo)
    resolver.recover()
    service = FakeService(ready=True)
    target = SymbolIntelligenceComposition(object(), resolver, object(), object(), object(), service, SimpleNamespace(admission_open=True))
    repo.values = (new,)
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovering = pool.submit(resolver.recover)
        assert repo.block.wait(1)
        result = target.enqueue_symbols(["AAA"])
        repo.release.set()
        recovering.result()
    assert result.accepted == 1
    assert service.calls[0][0].cik in {1, 2}


def test_ownership_loss_shutdown_incomplete_and_closed_reject_without_mutation():
    target, service = composition([identity("AAA", 1)], admission=False)
    before = target.target_diagnostics
    result = target.enqueue_symbols(["AAA"])
    after = target.target_diagnostics
    assert result.service_unavailable == 1 and not service.calls
    assert after.target_unique_issuers_admitted == before.target_unique_issuers_admitted == 0

    target.activation_state = "SHUTDOWN_INCOMPLETE"
    result = target.enqueue_symbols(["AAA"])
    assert result.service_unavailable == 1 and not service.calls
    target.activation_state = "CLOSED"
    result = target.enqueue_symbols(["AAA"])
    assert result.service_unavailable == 1 and not service.calls


def test_shadow_only_and_non_paper_style_unavailable_compositions_reject():
    shadow_only, shadow_service = composition([identity("AAA", 1)], admission=False)
    assert shadow_only.enqueue_symbols(["AAA"]).service_unavailable == 1
    assert not shadow_service.calls
    non_paper, non_paper_service = composition([identity("AAA", 1)], admission=False)
    non_paper.activation_state = "INELIGIBLE"
    assert non_paper.enqueue_symbols(["AAA"]).service_unavailable == 1
    assert not non_paper_service.calls


def test_ticker_readiness_failure_then_recovery_and_session_reset():
    target, service = composition([identity("AAA", 1)], ready=False)
    first = target.enqueue_symbols(["AAA", "BBB"])
    assert first.not_ready == 2 and not service.calls
    service.diagnostics.ticker_map_ready = True
    second = target.enqueue_symbols(["AAA"])
    assert second.accepted == 1
    fresh, _ = composition([identity("DDD", 4)])
    assert fresh.target_diagnostics.target_unique_issuers_admitted == 0
    assert fresh.enqueue_symbols(["DDD"]).accepted == 1


def test_target_counter_semantics_and_immutable_snapshot():
    target, service = composition([identity("AAA", 1)], ready=True)
    target.enqueue_symbols(["AAA", "BAD!", "ZZZZ"])
    target.enqueue_symbols(["AAA", "BBB", "CCC", "DDD"])
    diagnostics = target.target_diagnostics
    assert diagnostics.requests == 2
    assert diagnostics.requested_symbols == 7
    assert diagnostics.accepted == 1
    assert diagnostics.invalid == 1
    assert diagnostics.unresolved == 1
    assert diagnostics.rejected_limit == 4
    assert diagnostics.queue_rejected == 0
    assert diagnostics.target_unique_issuers_admitted == 1
    try:
        diagnostics.accepted = 99
    except (AttributeError, FrozenInstanceError):
        pass
    assert target.target_diagnostics.accepted == 1


def test_pre_ticker_and_service_unavailable_counters_do_not_resolve_or_queue():
    target, service = composition([identity("AAA", 1)], ready=False)
    assert target.enqueue_symbols(["AAA", "BAD!"]).not_ready == 2
    assert target.target_diagnostics.not_ready == 2
    assert target.resolver.calls == 0
    target.activation_state = "INELIGIBLE"
    assert target.enqueue_symbols(["AAA", "BBB"]).service_unavailable == 2
    assert target.target_diagnostics.service_unavailable == 2
    assert not service.calls

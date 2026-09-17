from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from types import SimpleNamespace

from app.configuration.models import SymbolIntelligenceSECEdgarConfiguration
from app.symbol_intelligence.composition import SymbolIntelligenceComposition
from app.symbol_intelligence.models import SecManualTargetStatus
from app.symbol_intelligence.providers.sec_identity import (
    SecIssuerIdentity,
    SecIssuerResolution,
    SecResolutionStatus,
    sec_issuer_id,
)


NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def _identity(symbol: str, cik: int) -> SecIssuerIdentity:
    return SecIssuerIdentity(symbol, cik, sec_issuer_id(cik), symbol, "Issuer", None, None, "rev", NOW, True)


class _Resolver:
    def __init__(self, identities):
        self._identities = {item.normalized_symbol: item for item in identities}

    def resolve_current(self, symbol):
        identity = self._identities.get(symbol)
        if identity is None:
            return SecIssuerResolution(symbol, symbol, SecResolutionStatus.UNRESOLVED, reason="CACHE_MISS")
        return SecIssuerResolution(symbol, symbol, SecResolutionStatus.RESOLVED, identity=identity)


class _Service:
    def __init__(self, targets, *, ready=False, fail_callback=False, accepted=True, start_result=True):
        self.configuration = SymbolIntelligenceSECEdgarConfiguration(
            enabled=True, user_agent="offline", manual_targets=targets,
        )
        self.diagnostics = SimpleNamespace(ticker_map_ready=ready, ticker_map_last_success_at=NOW)
        self.calls = []
        self.callback = None
        self.fail_callback = fail_callback
        self.accepted = accepted
        self.start_result = start_result

    def set_ticker_map_ready_callback(self, callback):
        self.callback = callback

    def clear_ticker_map_ready_callback(self):
        self.callback = None

    def current_time(self):
        return NOW

    def enqueue_issuer(self, identity, priority):
        self.calls.append((identity, priority))
        return self.accepted

    def start(self):
        return self.start_result

    def publish_ticker_ready(self):
        self.diagnostics.ticker_map_ready = True
        if self.callback is not None:
            if self.fail_callback:
                raise RuntimeError("callback probe")
            self.callback()

    def publish_ticker_failure(self):
        self.diagnostics.ticker_map_ready = False


def _composition(targets, identities, *, ready=False, admission=True):
    service = _Service(targets, ready=ready)
    ownership = SimpleNamespace(admission_open=admission, close=lambda **_: True)
    composition = SymbolIntelligenceComposition(
        object(), _Resolver(identities), object(), object(), object(), service, ownership,
    )
    composition.register_startup_target_callback()
    return composition, service


def test_startup_targets_wait_for_readiness_then_consume_once():
    composition, service = _composition(("AAA",), [_identity("AAA", 1)])
    assert composition.startup_target_diagnostics.pending is True
    service.publish_ticker_ready()
    diagnostics = composition.startup_target_diagnostics
    assert len(service.calls) == 1


def test_ticker_failure_keeps_targets_pending_until_later_success():
    composition, service = _composition(("AAA",), [_identity("AAA", 1)])
    assert composition.startup_target_diagnostics.pending is True
    assert composition.startup_target_diagnostics.attempted is False
    service.publish_ticker_failure()
    assert not service.calls
    assert composition.startup_target_diagnostics.pending is True
    service.publish_ticker_ready()
    service.publish_ticker_ready()
    assert len(service.calls) == 1
    assert composition.startup_target_diagnostics.consumed is True

class _RacingOwnership:
    def __init__(self):
        self.available = True
        self.entered = Barrier(2)
        self.close = lambda **_: True

    @property
    def admission_open(self):
        self.entered.wait()
        return self.available


def test_ticker_ready_ownership_race_returns_service_unavailable():
    service = _Service(("AAA",), ready=True)
    ownership = _RacingOwnership()
    composition = SymbolIntelligenceComposition(
        object(), _Resolver([_identity("AAA", 1)]), object(), object(), object(), service, ownership,
    )
    composition.register_startup_target_callback()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(composition._consume_startup_targets)
        ownership.available = False
        ownership.entered.wait()
        future.result()
    diagnostics = composition.startup_target_diagnostics
    assert diagnostics.consumed is True
    assert diagnostics.result is not None
    assert diagnostics.result.service_unavailable == 1
    assert not service.calls


def test_unresolved_startup_target_is_retained_without_retry():
    composition, service = _composition(("MISSING",), [])
    service.publish_ticker_ready()
    diagnostics = composition.startup_target_diagnostics
    assert diagnostics.result is not None
    assert diagnostics.result.entries[0].status is SecManualTargetStatus.UNRESOLVED
    assert diagnostics.consumed is True
    service.publish_ticker_ready()
    assert diagnostics.result is not None and not service.calls


class _AmbiguousResolver(_Resolver):
    def resolve_current(self, symbol):
        return SecIssuerResolution(symbol, symbol, SecResolutionStatus.AMBIGUOUS, reason="AMBIGUOUS")


def test_ambiguous_startup_target_is_retained_without_retry():
    service = _Service(("AAA",), ready=True)
    ownership = SimpleNamespace(admission_open=True, close=lambda **_: True)
    composition = SymbolIntelligenceComposition(
        object(), _AmbiguousResolver([]), object(), object(), object(), service, ownership,
    )
    composition.register_startup_target_callback()
    service.publish_ticker_ready()
    diagnostics = composition.startup_target_diagnostics
    assert diagnostics.result is not None
    assert diagnostics.result.entries[0].status is SecManualTargetStatus.AMBIGUOUS
    assert diagnostics.consumed is True and not service.calls


def test_queue_rejected_startup_target_consumes_intent_not_issuer_slot():
    service = _Service(("AAA",), ready=True, accepted=False)
    ownership = SimpleNamespace(admission_open=True, close=lambda **_: True)
    composition = SymbolIntelligenceComposition(
        object(), _Resolver([_identity("AAA", 1)]), object(), object(), object(), service, ownership,
    )
    composition.register_startup_target_callback()
    service.publish_ticker_ready()
    diagnostics = composition.startup_target_diagnostics
    assert diagnostics.result is not None and diagnostics.result.queue_rejected == 1
    assert diagnostics.consumed is True
    assert composition.target_diagnostics.target_unique_issuers_admitted == 0


def test_three_startup_targets_are_forwarded_in_one_batch_call(monkeypatch):
    identities = [_identity("AAA", 1), _identity("BBB", 2), _identity("CCC", 3)]
    composition, service = _composition(("AAA", "BBB", "CCC"), identities)
    calls = []
    original = SymbolIntelligenceComposition.enqueue_symbols
    monkeypatch.setattr(
        SymbolIntelligenceComposition,
        "enqueue_symbols",
        lambda self, symbols: (calls.append(tuple(symbols)), original(self, symbols))[1],
    )
    service.publish_ticker_ready()
    assert calls == [("AAA", "BBB", "CCC")]
    assert len(service.calls) == 3


def test_callback_clear_is_idempotent_and_prevents_resurrection():
    composition, service = _composition(("AAA",), [_identity("AAA", 1)])
    composition.close()
    composition.close()
    assert service.callback is None
    service.publish_ticker_ready()
    assert not service.calls


def test_worker_start_failure_clears_startup_bridge():
    composition, service = _composition(("AAA",), [_identity("AAA", 1)])
    service.start_result = False
    assert composition.start() is False
    assert service.callback is None
    service.publish_ticker_ready()
    diagnostics = composition.startup_target_diagnostics
    assert diagnostics.attempted is False and diagnostics.consumed is False
    assert not service.calls


def test_startup_targets_share_manual_three_issuer_cap():
    identities = [_identity("AAA", 1), _identity("BBB", 2), _identity("CCC", 3), _identity("DDD", 4)]
    composition, service = _composition(("AAA", "BBB"), identities)
    composition.register_startup_target_callback()
    service.publish_ticker_ready()
    assert composition.enqueue_symbols(("CCC",)).accepted == 1
    assert composition.enqueue_symbols(("DDD",)).rejected_limit == 1
    assert len(service.calls) == 3


def test_startup_callback_double_invocation_is_race_safe():
    composition, service = _composition(("AAA",), [_identity("AAA", 1)], ready=True)
    composition.register_startup_target_callback()
    barrier = Barrier(2)

    def invoke():
        barrier.wait()
        composition._consume_startup_targets()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: invoke(), range(2)))
    assert len(service.calls) == 1
    assert composition.startup_target_diagnostics.consumed is True


def test_startup_targets_are_inert_after_close_before_readiness():
    composition, service = _composition(("AAA",), [_identity("AAA", 1)])
    composition.close()
    service.publish_ticker_ready()
    diagnostics = composition.startup_target_diagnostics
    assert diagnostics.pending is False
    assert diagnostics.attempted is False
    assert not service.calls


def test_startup_invalid_target_is_completed_without_retry():
    composition, service = _composition(("?BAD",), [])
    service.publish_ticker_ready()
    diagnostics = composition.startup_target_diagnostics
    assert diagnostics.consumed is True
    assert diagnostics.result is not None
    assert diagnostics.result.entries[0].status is SecManualTargetStatus.INVALID
    service.publish_ticker_ready()
    assert len(service.calls) == 0

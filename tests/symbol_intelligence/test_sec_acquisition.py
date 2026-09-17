from datetime import UTC, datetime
from dataclasses import replace
import json
from threading import Event, Lock
import types
from concurrent.futures import ThreadPoolExecutor

from app.configuration.models import SymbolIntelligenceSECEdgarConfiguration
from app.symbol_intelligence import SymbolIntelligenceRepository
from app.symbol_intelligence.acquisition import (
    AcquisitionPriority,
    SecSymbolIntelligenceAcquisitionService,
)
from app.symbol_intelligence.providers.sec_filings import SecFilingFactNormalizer
from app.symbol_intelligence.providers.sec_identity import (
    SecIssuerIdentity,
    SecIssuerIdentityResolver,
    sec_issuer_id,
)
from app.symbol_intelligence.providers.sec_transport import (
    SecEdgarResponse,
)
from app.symbol_intelligence.models import IngestResult


NOW = datetime(2026, 9, 15, 20, tzinfo=UTC)


class FakeTransport:
    def __init__(self, ticker_payload: bytes, submissions: bytes):
        self.ticker_payload = ticker_payload
        self.submissions = submissions
        self.calls = []
        self._lock = Lock()

    def acquire(self, request):
        with self._lock:
            self.calls.append(request)
        content = self.ticker_payload if request.endpoint.value.startswith("TICKER") else self.submissions
        return type("Result", (), {"response": SecEdgarResponse(request.endpoint, 200, content, NOW, 1)})()

    def close(self):
        return None


def config(**overrides):
    values = dict(enabled=True, user_agent="offline-test", ticker_refresh_seconds=3600, submissions_refresh_seconds=3600)
    values.update(overrides)
    return SymbolIntelligenceSECEdgarConfiguration(**values)


def identity(cik=123456, symbol="ABC"):
    return SecIssuerIdentity(symbol, cik, sec_issuer_id(cik), symbol, "Issuer", None, None, "rev", NOW, True)


def submissions_payload(cik=123456):
    return json.dumps({"cik": str(cik), "filings": {"recent": {
        "accessionNumber": ["0001234567-26-000001"], "filingDate": ["2026-09-15"],
        "acceptanceDateTime": ["2026-09-15T19:00:00Z"], "form": ["8-K"],
        "primaryDocument": ["filing.htm"],
    }}}).encode()


def ticker_payload():
    return json.dumps({"data": [{"ticker": "ABC", "cik_str": "123456", "title": "Issuer"}]}).encode()


def service(tmp_path, transport=None, **cfg):
    repo = SymbolIntelligenceRepository(tmp_path / "si.sqlite3")
    resolver = SecIssuerIdentityResolver(repo)
    transport = transport or FakeTransport(ticker_payload(), submissions_payload())
    return SecSymbolIntelligenceAcquisitionService(config(**cfg), repo, transport, resolver), transport, repo, resolver


def test_empty_repository_and_disabled_are_safe(tmp_path):
    service_obj, transport, _, resolver = service(tmp_path, enabled=False)
    assert service_obj.recover() == 0
    assert service_obj.start() is True
    service_obj.stop()
    assert resolver.size == 0
    assert not transport.calls


def test_ticker_refresh_applies_and_recovers_cache(tmp_path):
    service_obj, transport, _, resolver = service(tmp_path)
    service_obj.run_once()
    assert resolver.size == 1
    assert transport.calls[0].endpoint.value == "TICKER_MAP_EXCHANGE"
    assert service_obj.metrics.ticker_refresh_successes == 1


def test_resolver_recover_failure_is_diagnosed_after_map_commit(tmp_path):
    repo = SymbolIntelligenceRepository(tmp_path / "si.sqlite3")
    transport = FakeTransport(ticker_payload(), submissions_payload())
    calls = {"count": 0}

    class FlakyResolver:
        size = 0
        def recover(self):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("cache failure")
            self.size = 1
            return 1

    resolver = FlakyResolver()
    service_obj = SecSymbolIntelligenceAcquisitionService(config(), repo, transport, resolver)
    service_obj._next_ticker_due = 0
    service_obj.run_once()
    assert service_obj.metrics.resolver_recover_failures == 1
    assert repo.recover_current_identities(limit=10)
    assert resolver.recover() == 1


def test_cik_coalescing_and_fact_persistence(tmp_path):
    service_obj, transport, repo, _ = service(tmp_path)
    ident = identity()
    assert service_obj.enqueue_issuer(ident, AcquisitionPriority.WATCH)
    assert service_obj.enqueue_issuer(ident, AcquisitionPriority.HIGH_PRIORITY)
    service_obj.run_once()
    submissions_calls = [c for c in transport.calls if c.endpoint.value == "SUBMISSIONS"]
    assert len(submissions_calls) == 1
    assert service_obj.metrics.issuer_refresh_coalesced == 1
    assert repo.recent_events("ABC", limit=10)
    state = repo.get_sec_issuer_acquisition_state(ident.issuer_id)
    assert state is not None and state.status.value == "COMPLETE"


def test_zero_fact_submissions_still_complete_issuer(tmp_path):
    empty = json.dumps({"cik": "123456", "filings": {"recent": {
        "accessionNumber": [], "filingDate": [], "acceptanceDateTime": [], "form": [], "primaryDocument": [],
    }}}).encode()
    service_obj, _, repo, _ = service(tmp_path, transport=FakeTransport(ticker_payload(), empty), ticker_refresh_seconds=999999)
    ident = identity()
    service_obj.enqueue_issuer(ident)
    service_obj.run_once()
    state = repo.get_sec_issuer_acquisition_state(ident.issuer_id)
    assert state is not None and state.status.value == "COMPLETE"
    assert state.last_complete_observation_at is not None


def test_cycle_budget_limits_submissions(tmp_path):
    service_obj, transport, _, _ = service(tmp_path, ticker_refresh_seconds=999999)
    for cik in range(1, 40):
        service_obj.enqueue_issuer(identity(cik=cik, symbol=f"S{cik}"))
    service_obj.run_once()
    assert len([c for c in transport.calls if c.endpoint.value == "SUBMISSIONS"]) <= 32
    assert service_obj.diagnostics.queue_size >= 7


def test_queue_capacity_drops_lower_priority(tmp_path):
    service_obj, _, _, _ = service(tmp_path, ticker_refresh_seconds=999999)
    for cik in range(1, 257):
        assert service_obj.enqueue_issuer(identity(cik=cik, symbol=f"S{cik}"), AcquisitionPriority.HIGH_PRIORITY)
    assert not service_obj.enqueue_issuer(identity(cik=999999, symbol="LOW"), AcquisitionPriority.WATCH)
    assert service_obj.diagnostics.queue_size == 256
    assert service_obj.metrics.issuer_refresh_dropped == 1


def test_start_stop_close_are_idempotent(tmp_path):
    service_obj, _, _, _ = service(tmp_path)
    assert service_obj.stop()
    assert service_obj.start()
    assert not service_obj.start()
    assert service_obj.stop()
    assert service_obj.stop()
    assert service_obj.close()
    assert service_obj.close()


def test_close_timeout_is_truthful_and_retryable(tmp_path):
    entered, release = Event(), Event()

    class Blocking(FakeTransport):
        def acquire(self, request):
            entered.set()
            release.wait(2)
            return super().acquire(request)

    service_obj, _, _, _ = service(tmp_path, transport=Blocking(ticker_payload(), submissions_payload()), ticker_refresh_seconds=999999)
    service_obj._next_ticker_due = 10**9
    service_obj.enqueue_issuer(identity())
    assert service_obj.start()
    assert entered.wait(1)
    assert service_obj.close(0.01) is False
    assert service_obj.diagnostics.shutdown_incomplete is True
    assert service_obj.diagnostics.worker_alive is True
    assert service_obj.enqueue_issuer(identity(cik=999, symbol="ZZZ")) is False
    release.set()
    assert service_obj.close(2.0) is True
    assert service_obj.diagnostics.worker_alive is False


def test_stop_rejects_enqueue_while_active_work_is_stopping(tmp_path):
    entered, release = Event(), Event()

    class Blocking(FakeTransport):
        def acquire(self, request):
            entered.set()
            release.wait(2)
            return super().acquire(request)

    service_obj, _, _, _ = service(tmp_path, transport=Blocking(ticker_payload(), submissions_payload()), ticker_refresh_seconds=999999)
    service_obj._next_ticker_due = 10**9
    service_obj.enqueue_issuer(identity())
    assert service_obj.start() and entered.wait(1)
    assert service_obj.stop(0.01) is False
    assert service_obj.enqueue_issuer(identity(cik=999, symbol="ZZZ")) is False
    assert service_obj.diagnostics.shutdown_incomplete is True
    release.set()
    assert service_obj.stop(2.0) is True


def test_worker_survives_unexpected_cycle_exception(tmp_path):
    service_obj, _, repo, _ = service(tmp_path, ticker_refresh_seconds=999999)
    service_obj._next_ticker_due = 10**9
    called = Event()
    count = {"value": 0}
    original = service_obj.run_once

    def cycle():
        count["value"] += 1
        if count["value"] == 1:
            raise RuntimeError("secret must not escape")
        original()
        if service_obj.metrics.issuer_acquisition_successes:
            called.set()

    service_obj.run_once = cycle
    service_obj.enqueue_issuer(identity())
    assert service_obj.start()
    assert called.wait(1)
    service_obj.stop()
    assert service_obj.metrics.unexpected_worker_failures == 1
    assert service_obj.metrics.issuer_acquisition_successes == 1
    assert service_obj.diagnostics.recent_failure_counts == (("INTERNAL_ERROR", 1),)
    assert repo.recent_events("ABC", limit=2)


def test_concurrent_starters_create_one_worker(tmp_path):
    service_obj, _, _, _ = service(tmp_path, enabled=False)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service_obj.start(), range(8)))
    assert sum(results) == 1
    service_obj.close()


def test_start_close_race_has_single_coherent_terminal_state(tmp_path):
    service_obj, _, _, _ = service(tmp_path, enabled=False)
    barrier = __import__("threading").Barrier(2)

    def do_start():
        barrier.wait()
        return service_obj.start()

    def do_close():
        barrier.wait()
        return service_obj.close(2.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        started, closed = pool.submit(do_start), pool.submit(do_close)
        started_result, closed_result = started.result(), closed.result()
    assert isinstance(started_result, bool) and isinstance(closed_result, bool)
    assert service_obj.diagnostics.worker_alive is False
    assert service_obj.start() is False


def test_concurrent_enqueue_preserves_capacity_and_priority(tmp_path):
    service_obj, _, _, _ = service(tmp_path)
    service_obj._next_ticker_due = 10**9
    identities = [identity(cik=i + 1, symbol=f"S{i + 1}") for i in range(300)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda item: service_obj.enqueue_issuer(item[0], item[1]),
                      [(item, AcquisitionPriority.WATCH if i % 2 else AcquisitionPriority.HIGH_PRIORITY) for i, item in enumerate(identities)]))
    assert service_obj.diagnostics.queue_size <= 256
    assert service_obj.enqueue_issuer(identities[0], AcquisitionPriority.ACTIVE_OPPORTUNITY)
    assert service_obj.diagnostics.queue_size <= 256
    assert service_obj.metrics.issuer_refresh_dropped + service_obj.metrics.evicted_count >= 44


def test_normalized_events_are_written_in_bounded_batches(tmp_path):
    service_obj, transport, _, _ = service(tmp_path, ticker_refresh_seconds=999999)
    service_obj._next_ticker_due = 10**9
    base = SecFilingFactNormalizer().normalize(identity(), submissions_payload(), NOW).events[0]
    events = tuple(replace(base, event_id=f"SEC_EDGAR:{i:018d}", source_id=f"0001234567-26-{i:06d}") for i in range(2001))
    batches = []
    lifecycle = []

    class BatchRepo:
        def append_evidence(self, batch):
            batches.append(tuple(batch))
            lifecycle.append(f"batch-{len(batches)}")
            return IngestResult(inserted=len(batch))
        def store_source_state(self, state):
            return True
        def record_sec_issuer_acquisition_attempt(self, issuer_id, attempted_at):
            lifecycle.append("attempt")
        def complete_sec_issuer_acquisition(self, issuer_id, completed_at, *, next_due_at=None):
            lifecycle.append("complete")
        def fail_sec_issuer_acquisition(self, issuer_id, attempted_at, *, failure_category):
            lifecycle.append("failure")

    service_obj.repository = BatchRepo()
    service_obj.normalizer = types.SimpleNamespace(normalize=lambda *args: types.SimpleNamespace(events=events, failure=None))
    service_obj.enqueue_issuer(identity())
    service_obj.run_once()
    assert [len(batch) for batch in batches] == [1000, 1000, 1]
    assert service_obj.metrics.facts_emitted == 2001
    assert service_obj.metrics.facts_inserted == 2001
    assert len(transport.calls) == 1
    assert lifecycle == ["attempt", "batch-1", "batch-2", "batch-3", "complete"]


def test_multi_batch_failure_retries_without_duplicates(tmp_path):
    service_obj, _, _, _ = service(tmp_path, ticker_refresh_seconds=999999)
    service_obj._next_ticker_due = 10**9
    base = SecFilingFactNormalizer().normalize(identity(), submissions_payload(), NOW).events[0]
    events = tuple(replace(base, event_id=f"SEC_EDGAR:{i:018d}", source_id=f"0001234567-26-{i:06d}") for i in range(1001))
    seen, calls, lifecycle = set(), [], []

    class FlakyRepo:
        failed = False
        def append_evidence(self, batch):
            calls.append(len(batch))
            lifecycle.append(f"batch-{len(calls)}")
            if len(calls) == 2 and not self.failed:
                self.failed = True
                raise RuntimeError("injected batch failure")
            inserted = sum(event.event_id not in seen for event in batch)
            seen.update(event.event_id for event in batch)
            return IngestResult(inserted=inserted, deduplicated=len(batch) - inserted)
        def store_source_state(self, state):
            return True
        def record_sec_issuer_acquisition_attempt(self, issuer_id, attempted_at):
            lifecycle.append("attempt")
        def complete_sec_issuer_acquisition(self, issuer_id, completed_at, *, next_due_at=None):
            lifecycle.append("complete")
        def fail_sec_issuer_acquisition(self, issuer_id, attempted_at, *, failure_category):
            lifecycle.append("failure")

    service_obj.repository = FlakyRepo()
    service_obj.normalizer = types.SimpleNamespace(normalize=lambda *args: types.SimpleNamespace(events=events, failure=None))
    service_obj.enqueue_issuer(identity())
    service_obj.run_once()
    assert calls == [1000, 1]
    assert "complete" not in lifecycle
    for target in service_obj._targets.values():
        target.next_due = 0
    service_obj.run_once()
    assert calls == [1000, 1, 1000, 1]
    assert len(seen) == 1001
    assert lifecycle[-4:] == ["attempt", "batch-3", "batch-4", "complete"]


def test_unresolved_identity_rejected_without_transport(tmp_path):
    service_obj, transport, _, _ = service(tmp_path)
    assert not service_obj.enqueue_issuer(object())
    assert service_obj.metrics.unresolved_identity_requests == 1
    assert not transport.calls


def test_ticker_map_readiness_is_session_scoped_and_published_after_source_state(tmp_path):
    service_obj, _, _, _ = service(tmp_path)
    before = service_obj.diagnostics
    assert before.ticker_map_ready is False
    assert before.ticker_map_last_success_at is None
    service_obj.run_once()
    after = service_obj.diagnostics
    assert after.ticker_map_ready is True
    assert after.ticker_map_last_success_at is not None
    assert after.ticker_map_last_success_at.tzinfo is not None


def test_ticker_ready_callback_runs_after_publication_and_outside_service_lock(tmp_path):
    service_obj, _, _, _ = service(tmp_path)
    observations = []

    def callback():
        observations.append(service_obj.diagnostics.ticker_map_ready)
        acquired = service_obj._lock.acquire(blocking=False)
        if acquired:
            service_obj._lock.release()
        observations.append(acquired)

    service_obj.set_ticker_map_ready_callback(callback)
    service_obj.run_once()
    assert observations == [True, True]


def test_ticker_ready_callback_only_fires_after_complete_success(tmp_path):
    service_obj, transport, repo, _ = service(tmp_path)
    calls = []
    service_obj.set_ticker_map_ready_callback(lambda: calls.append("ready"))
    transport.acquire = lambda request: type("Result", (), {"response": None})()
    service_obj.run_once()
    assert calls == []
    transport = service_obj.transport
    transport.acquire = FakeTransport(ticker_payload(), submissions_payload()).acquire
    repo.store_source_state = lambda state: False
    service_obj._next_ticker_due = 0
    service_obj.run_once()
    assert calls == []


def test_ticker_ready_callback_failure_isolated(tmp_path):
    service_obj, _, _, _ = service(tmp_path)
    service_obj.set_ticker_map_ready_callback(lambda: (_ for _ in ()).throw(RuntimeError("probe")))
    service_obj.run_once()
    assert service_obj.diagnostics.ticker_map_ready is True
    assert service_obj.diagnostics.ticker_ready_callback_failures == 1


def test_ticker_map_readiness_stays_false_when_source_state_fails(tmp_path):
    service_obj, _, repo, _ = service(tmp_path)
    original = repo.store_source_state
    repo.store_source_state = lambda state: False
    service_obj.run_once()
    assert service_obj.diagnostics.ticker_map_ready is False
    assert service_obj.diagnostics.ticker_map_last_success_at is None
    repo.store_source_state = original
    service_obj._next_ticker_due = 0
    service_obj.run_once()
    assert service_obj.diagnostics.ticker_map_ready is True


def test_ticker_map_readiness_survives_later_refresh_failure(tmp_path):
    service_obj, transport, _, _ = service(tmp_path)
    service_obj.run_once()
    assert service_obj.diagnostics.ticker_map_ready is True
    success_at = service_obj.diagnostics.ticker_map_last_success_at
    transport.acquire = lambda request: type("Result", (), {"response": None})()
    service_obj._next_ticker_due = 0
    service_obj.run_once()
    assert service_obj.diagnostics.ticker_map_ready is True
    assert service_obj.diagnostics.ticker_map_last_success_at == success_at

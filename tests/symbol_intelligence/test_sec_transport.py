from datetime import UTC, datetime, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.configuration.models import SymbolIntelligenceSECEdgarConfiguration
from app.symbol_intelligence.providers.sec_identity import parse_sec_ticker_map
from app.symbol_intelligence.providers.sec_transport import (
    SecAcquisitionFailureKind,
    SecEdgarEndpointClass,
    SecEdgarRateLimiter,
    SecEdgarRequest,
    SecEdgarTransport,
)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
    def monotonic(self): return self.now
    def sleep(self, value): self.sleeps.append(value); self.now += value


class Response:
    def __init__(self, status=200, content=b"{}", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
    def iter_bytes(self):
        yield self.content


class Client:
    def __init__(self, outcomes): self.outcomes, self.calls, self.closed = list(outcomes), [], 0
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException): raise outcome
        return outcome
    def close(self): self.closed += 1


def config(**overrides):
    values = dict(enabled=True, user_agent="SECRET-UA-SENTINEL-DO-NOT-LEAK")
    values.update(overrides)
    return SymbolIntelligenceSECEdgarConfiguration(**values)


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("real network access is forbidden in Phase-2C tests")
    monkeypatch.setattr(httpx.Client, "send", blocked)
    monkeypatch.setattr(httpx, "request", blocked)


def transport(outcomes, clock=None, limiter=None, wall_clock=None, **overrides):
    clock = clock or Clock()
    client = Client(outcomes)
    value = SecEdgarTransport(config(**overrides), client=client, limiter=limiter or SecEdgarRateLimiter(2, monotonic=clock.monotonic, sleeper=clock.sleep), monotonic=clock.monotonic, sleeper=clock.sleep, wall_clock=wall_clock or (lambda: datetime.now(UTC)))
    return value, client, clock


def test_endpoint_builders_allow_only_typed_sec_hosts():
    assert SecEdgarRequest.ticker_map().path == "https://www.sec.gov/files/company_tickers_exchange.json"
    assert SecEdgarRequest.ticker_map(False).endpoint is SecEdgarEndpointClass.TICKER_MAP_LEGACY
    assert SecEdgarRequest.submissions(123456).path.endswith("CIK0000123456.json")
    with pytest.raises(ValueError):
        SecEdgarRequest(SecEdgarEndpointClass.SUBMISSIONS, "https://example.com/x", 10)


def test_success_and_phase2b_parser_seam():
    body = b'{"0":{"ticker":"AAA","cik_str":1,"title":"Issuer"}}'
    value, client, _ = transport([Response(content=body)])
    result = value.acquire(SecEdgarRequest.ticker_map())
    assert result.response.content == body and result.response.attempts == 1
    parsed = parse_sec_ticker_map(result.response.content, source="SEC_EDGAR", observed_at=result.response.observed_at)
    assert parsed.identities[0].normalized_symbol == "AAA"
    value.close(); value.close(); assert client.closed == 0  # injected client is caller-owned


@pytest.mark.parametrize("outcome,kind", [(httpx.TimeoutException("x"), SecAcquisitionFailureKind.TIMEOUT), (httpx.ConnectError("x"), SecAcquisitionFailureKind.CONNECTION_ERROR)])
def test_retryable_transport_failures(outcome, kind):
    value, client, _ = transport([outcome, Response()])
    result = value.acquire(SecEdgarRequest.ticker_map())
    assert result.response is not None and len(client.calls) == 2


@pytest.mark.parametrize("status", [403, 404])
def test_client_errors_do_not_retry(status):
    value, client, _ = transport([Response(status=status), Response()])
    result = value.acquire(SecEdgarRequest.ticker_map())
    assert result.failure.kind is SecAcquisitionFailureKind.HTTP_CLIENT_ERROR and len(client.calls) == 1


def test_malformed_and_oversized_do_not_retry():
    value, client, _ = transport([Response(content=b"not-json")])
    assert value.acquire(SecEdgarRequest.ticker_map()).failure.kind is SecAcquisitionFailureKind.MALFORMED_RESPONSE
    assert len(client.calls) == 1


@pytest.mark.parametrize("endpoint,limit", [(SecEdgarEndpointClass.TICKER_MAP_EXCHANGE, 16 * 1024 * 1024), (SecEdgarEndpointClass.SUBMISSIONS, 8 * 1024 * 1024)])
def test_exact_body_limit_is_accepted(endpoint, limit):
    path = "https://www.sec.gov/files/company_tickers_exchange.json" if endpoint is SecEdgarEndpointClass.TICKER_MAP_EXCHANGE else "https://data.sec.gov/submissions/CIK0000000001.json"
    request = SecEdgarRequest(endpoint, path, limit)
    value, _, _ = transport([Response(content=b"{" + b" " * (limit - 2) + b"}")])
    result = value.acquire(request)
    assert result.response is not None and len(result.response.content) == limit


@pytest.mark.parametrize("endpoint,limit", [(SecEdgarEndpointClass.TICKER_MAP_EXCHANGE, 16 * 1024 * 1024), (SecEdgarEndpointClass.SUBMISSIONS, 8 * 1024 * 1024)])
def test_one_byte_over_body_limit_is_rejected_without_retry(endpoint, limit):
    path = "https://www.sec.gov/files/company_tickers_exchange.json" if endpoint is SecEdgarEndpointClass.TICKER_MAP_EXCHANGE else "https://data.sec.gov/submissions/CIK0000000001.json"
    request = SecEdgarRequest(endpoint, path, limit)
    value, client, _ = transport([Response(content=b"{" + b" " * (limit - 1) + b"}")])
    result = value.acquire(request)
    assert result.failure.kind is SecAcquisitionFailureKind.OVERSIZED_RESPONSE
    assert result.response is None and len(client.calls) == 1
    value, client, _ = transport([Response(content=b"x" * 20)], read_timeout_seconds=1)
    request = SecEdgarRequest(SecEdgarEndpointClass.SUBMISSIONS, "https://data.sec.gov/submissions/CIK0000000001.json", 10)
    assert value.acquire(request).failure.kind is SecAcquisitionFailureKind.OVERSIZED_RESPONSE
    assert len(client.calls) == 1


def test_retry_after_is_capped_and_uses_max_of_backoff():
    clock = Clock()
    value, client, _ = transport([Response(status=429, headers={"Retry-After": "90"}), Response()], clock=clock)
    assert value.acquire(SecEdgarRequest.ticker_map()).response is not None
    assert clock.sleeps[0] == 60.0


def test_shared_limiter_coordinates_instances():
    clock = Clock(); limiter = SecEdgarRateLimiter(2, monotonic=clock.monotonic, sleeper=clock.sleep)
    first, _, _ = transport([Response()], clock=clock, limiter=limiter)
    second, _, _ = transport([Response()], clock=clock, limiter=limiter)
    first.acquire(SecEdgarRequest.ticker_map()); second.acquire(SecEdgarRequest.submissions(1))
    assert clock.sleeps and clock.sleeps[0] == 0.5


def test_concurrent_shared_limiter_calls_are_spaced():
    clock = Clock(); limiter = SecEdgarRateLimiter(2, monotonic=clock.monotonic, sleeper=clock.sleep)
    first, first_client, _ = transport([Response()], clock=clock, limiter=limiter)
    second, second_client, _ = transport([Response()], clock=clock, limiter=limiter)
    barrier = __import__("threading").Barrier(2)
    def call(value):
        barrier.wait()
        return value.acquire(SecEdgarRequest.ticker_map()).response is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert tuple(pool.map(call, (first, second))) == (True, True)
    assert len(first_client.calls) == len(second_client.calls) == 1
    assert clock.sleeps and min(clock.sleeps) >= 0.5


def test_cooldown_after_three_qualifying_failures_and_recovery():
    clock = Clock(); value, client, _ = transport([httpx.TimeoutException("x")] * 3, clock=clock, max_retries=0, failure_cooldown_seconds=10)
    request = SecEdgarRequest.ticker_map()
    for _ in range(3): assert value.acquire(request).failure.kind is SecAcquisitionFailureKind.TIMEOUT
    assert value.acquire(request).failure.kind is SecAcquisitionFailureKind.COOLDOWN_ACTIVE
    assert len(client.calls) == 3
    clock.now += 10
    client.outcomes.append(Response())
    assert value.acquire(request).response is not None


def test_concurrent_cooldown_state_counts_failed_acquisitions():
    clock = Clock(); value, client, _ = transport([httpx.TimeoutException("x")] * 3, clock=clock, max_retries=0, failure_cooldown_seconds=10)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = tuple(pool.map(lambda _: value.acquire(SecEdgarRequest.ticker_map()).failure.kind, range(3)))
    assert all(kind is SecAcquisitionFailureKind.TIMEOUT for kind in results)
    assert value.acquire(SecEdgarRequest.ticker_map()).failure.kind is SecAcquisitionFailureKind.COOLDOWN_ACTIVE
    assert len(client.calls) == 3


def test_retry_attempt_metrics_are_counted_without_affecting_cooldown_acquisitions():
    value, _, _ = transport([Response(status=500), Response()])
    assert value.acquire(SecEdgarRequest.ticker_map()).response is not None
    metrics = value.metrics
    assert (metrics.http_attempts, metrics.retry_attempts, metrics.server_errors, metrics.successful_acquisitions) == (2, 1, 1, 1)


@pytest.mark.parametrize("outcome,field", [(Response(status=429), "rate_limited"), (httpx.TimeoutException("x"), "timeouts"), (httpx.ConnectError("x"), "connection_errors")])
def test_intermediate_failure_metrics(outcome, field):
    value, _, _ = transport([outcome, Response()])
    assert value.acquire(SecEdgarRequest.ticker_map()).response is not None
    assert getattr(value.metrics, field) == 1


def test_disabled_and_user_agent_secret_safe(caplog):
    value, _, _ = transport([Response()], enabled=False)
    result = value.acquire(SecEdgarRequest.ticker_map())
    assert result.failure.kind is SecAcquisitionFailureKind.DISABLED
    value, _, _ = transport([Response()])
    text = repr(value) + repr(result) + repr(value.metrics)
    assert "SECRET-UA-SENTINEL-DO-NOT-LEAK" not in text


def test_http_date_retry_after_uses_injected_wall_clock():
    clock = Clock(); wall = lambda: datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
    future = "Tue, 15 Sep 2026 20:00:10 GMT"
    value, _, _ = transport([Response(status=503, headers={"Retry-After": future}), Response()], clock=clock, wall_clock=wall)
    assert value.acquire(SecEdgarRequest.ticker_map()).response is not None
    assert clock.sleeps[0] == 10.0

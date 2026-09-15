"""Bounded, offline-testable SEC EDGAR transport primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
import json
import math
from threading import Lock
import time
from urllib.parse import urlsplit

import httpx

from .sec_identity import sec_cik_path


TICKER_BODY_LIMIT = 16 * 1024 * 1024
SUBMISSIONS_BODY_LIMIT = 8 * 1024 * 1024
RETRY_AFTER_CAP = 60.0
JITTER_MAX = 0.250
COOLDOWN_THRESHOLD = 3
ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})


class SecEdgarEndpointClass(StrEnum):
    TICKER_MAP_EXCHANGE = "TICKER_MAP_EXCHANGE"
    TICKER_MAP_LEGACY = "TICKER_MAP_LEGACY"
    SUBMISSIONS = "SUBMISSIONS"


class SecAcquisitionFailureKind(StrEnum):
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    HTTP_RATE_LIMITED = "HTTP_RATE_LIMITED"
    HTTP_SERVER_ERROR = "HTTP_SERVER_ERROR"
    HTTP_CLIENT_ERROR = "HTTP_CLIENT_ERROR"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    OVERSIZED_RESPONSE = "OVERSIZED_RESPONSE"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    DISABLED = "DISABLED"
    INVALID_REQUEST = "INVALID_REQUEST"


@dataclass(frozen=True, slots=True)
class SecEdgarRequest:
    endpoint: SecEdgarEndpointClass
    path: str
    body_limit: int

    @classmethod
    def ticker_map(cls, exchange_aware: bool = True) -> "SecEdgarRequest":
        endpoint = SecEdgarEndpointClass.TICKER_MAP_EXCHANGE if exchange_aware else SecEdgarEndpointClass.TICKER_MAP_LEGACY
        filename = "company_tickers_exchange.json" if exchange_aware else "company_tickers.json"
        return cls(endpoint, f"https://www.sec.gov/files/{filename}", TICKER_BODY_LIMIT)

    @classmethod
    def submissions(cls, cik: int) -> "SecEdgarRequest":
        return cls(SecEdgarEndpointClass.SUBMISSIONS, f"https://data.sec.gov/submissions/{sec_cik_path(cik)}", SUBMISSIONS_BODY_LIMIT)

    def __post_init__(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path:
            raise ValueError("SEC request URL is not allowed")
        if self.body_limit <= 0:
            raise ValueError("SEC request body limit must be positive")


@dataclass(frozen=True, slots=True)
class SecEdgarResponse:
    endpoint: SecEdgarEndpointClass
    status_code: int
    content: bytes
    observed_at: datetime
    attempts: int
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class SecAcquisitionFailure:
    kind: SecAcquisitionFailureKind
    endpoint: SecEdgarEndpointClass | None
    observed_at: datetime
    attempts: int
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class SecAcquisitionResult:
    response: SecEdgarResponse | None = None
    failure: SecAcquisitionFailure | None = None


@dataclass(frozen=True, slots=True)
class SecTransportMetrics:
    http_attempts: int = 0
    successful_acquisitions: int = 0
    retry_attempts: int = 0
    rate_limited: int = 0
    server_errors: int = 0
    timeouts: int = 0
    connection_errors: int = 0
    cooldown_rejections: int = 0
    oversized_responses: int = 0
    malformed_responses: int = 0
    bytes_received: int = 0
    latency_seconds: float = 0.0
    last_success_at: datetime | None = None


class SecEdgarRateLimiter:
    def __init__(self, requests_per_second: float = 2.0, *, monotonic: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
        if not math.isfinite(requests_per_second) or requests_per_second <= 0 or requests_per_second > 5:
            raise ValueError("requests_per_second must be in (0,5]")
        self._interval = 1.0 / requests_per_second
        self._clock, self._sleeper = monotonic, sleeper
        self._last: float | None = None
        self._lock = Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last is not None:
                delay = self._interval - (now - self._last)
                if delay > 0:
                    self._sleeper(delay)
                    now = self._clock()
            self._last = now


class _Circuit:
    def __init__(self, cooldown_seconds: float, *, monotonic: Callable[[], float]) -> None:
        self.cooldown = cooldown_seconds
        self.clock = monotonic
        self.failures = 0
        self.until = 0.0
        self.lock = Lock()

    def active(self) -> bool:
        with self.lock:
            return self.until > self.clock()

    def failure(self) -> None:
        with self.lock:
            self.failures += 1
            if self.failures >= COOLDOWN_THRESHOLD:
                self.until = self.clock() + self.cooldown

    def success(self) -> None:
        with self.lock:
            self.failures = 0
            self.until = 0.0


class SecEdgarTransport:
    """Synchronous transport; callers own scheduling and never use this on hot paths."""

    def __init__(self, configuration: object, *, client: object | None = None, limiter: SecEdgarRateLimiter | None = None, monotonic: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep, jitter: Callable[[], float] = lambda: 0.0, wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC), owns_client: bool | None = None) -> None:
        self._config = configuration
        self._enabled = bool(getattr(configuration, "enabled", False))
        self._user_agent = str(getattr(configuration, "user_agent", ""))
        self._connect_timeout = float(getattr(configuration, "connect_timeout_seconds"))
        self._read_timeout = float(getattr(configuration, "read_timeout_seconds"))
        self._max_retries = int(getattr(configuration, "max_retries"))
        self._backoff_initial = float(getattr(configuration, "backoff_initial_seconds"))
        self._backoff_max = float(getattr(configuration, "backoff_max_seconds"))
        self._limiter = limiter or SecEdgarRateLimiter(float(getattr(configuration, "requests_per_second")), monotonic=monotonic, sleeper=sleeper)
        self._circuit = _Circuit(float(getattr(configuration, "failure_cooldown_seconds")), monotonic=monotonic)
        self._monotonic, self._sleeper, self._jitter, self._wall_clock = monotonic, sleeper, jitter, wall_clock
        self._client = client if client is not None else httpx.Client(timeout=self._timeout(), follow_redirects=True)
        self._owns_client = client is None if owns_client is None else owns_client
        self._closed = False
        self._metrics = SecTransportMetrics()
        self._metrics_lock = Lock()

    @property
    def metrics(self) -> SecTransportMetrics:
        with self._metrics_lock:
            return self._metrics

    def acquire(self, request: SecEdgarRequest) -> SecAcquisitionResult:
        observed = self._wall_clock().astimezone(UTC)
        if not self._enabled:
            return self._failure(SecAcquisitionFailureKind.DISABLED, request, 0, observed)
        if self._closed:
            return self._failure(SecAcquisitionFailureKind.INVALID_REQUEST, request, 0, observed)
        if self._circuit.active():
            self._inc("cooldown_rejections")
            return self._failure(SecAcquisitionFailureKind.COOLDOWN_ACTIVE, request, 0, observed)
        attempts = 0
        for attempt in range(self._max_retries + 1):
            attempts += 1
            self._limiter.wait()
            started = self._monotonic()
            self._inc("http_attempts")
            try:
                status, headers, content = self._send(request)
                if content is None:
                    self._inc("oversized_responses")
                    return self._failure(SecAcquisitionFailureKind.OVERSIZED_RESPONSE, request, attempts, observed, status)
                if status == 429:
                    kind = SecAcquisitionFailureKind.HTTP_RATE_LIMITED
                elif status >= 500:
                    kind = SecAcquisitionFailureKind.HTTP_SERVER_ERROR
                elif status >= 400:
                    kind = SecAcquisitionFailureKind.HTTP_CLIENT_ERROR
                elif status < 200:
                    kind = SecAcquisitionFailureKind.HTTP_CLIENT_ERROR
                else:
                    try:
                        json.loads(content)
                    except (TypeError, ValueError):
                        self._inc("malformed_responses")
                        return self._failure(SecAcquisitionFailureKind.MALFORMED_RESPONSE, request, attempts, observed, status)
                    result = SecEdgarResponse(request.endpoint, status, content, observed, attempts, headers)
                    self._circuit.success()
                    self._update_success(len(content), self._monotonic() - started)
                    return SecAcquisitionResult(response=result)
                if kind in {SecAcquisitionFailureKind.HTTP_RATE_LIMITED, SecAcquisitionFailureKind.HTTP_SERVER_ERROR} and attempt < self._max_retries:
                    self._record_attempt_metric(kind)
                    self._retry_delay(attempt, headers, observed)
                    self._inc("retry_attempts")
                    continue
                self._qualifying(kind)
                return self._failure(kind, request, attempts, observed, status)
            except httpx.TimeoutException:
                kind = SecAcquisitionFailureKind.TIMEOUT
            except httpx.RequestError:
                kind = SecAcquisitionFailureKind.CONNECTION_ERROR
            except Exception:
                kind = SecAcquisitionFailureKind.CONNECTION_ERROR
            if attempt < self._max_retries:
                self._record_attempt_metric(kind)
                self._retry_delay(attempt, (), observed)
                self._inc("retry_attempts")
                continue
            self._qualifying(kind)
            return self._failure(kind, request, attempts, observed)
        return self._failure(SecAcquisitionFailureKind.CONNECTION_ERROR, request, attempts, observed)

    def _send(self, request: SecEdgarRequest) -> tuple[int, tuple[tuple[str, str], ...], bytes | None]:
        kwargs = {"headers": {"User-Agent": self._user_agent, "Accept": "application/json"}, "timeout": self._timeout()}
        stream = getattr(self._client, "stream", None)
        if callable(stream):
            with stream("GET", request.path, **kwargs) as response:
                status = int(getattr(response, "status_code", 0))
                headers = tuple((str(k).casefold(), str(v)) for k, v in getattr(response, "headers", {}).items())
                return status, headers, _bounded_content(response, request.body_limit)
        response = self._client.get(request.path, **kwargs)
        status = int(getattr(response, "status_code", 0))
        headers = tuple((str(k).casefold(), str(v)) for k, v in getattr(response, "headers", {}).items())
        return status, headers, _bounded_content(response, request.body_limit)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            close = getattr(self._client, "close", None)
            if callable(close):
                close()

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(connect=self._connect_timeout, read=self._read_timeout, write=self._read_timeout, pool=self._connect_timeout)

    def _retry_delay(self, attempt: int, headers: tuple[tuple[str, str], ...], observed: datetime) -> None:
        delay = min(self._backoff_max, self._backoff_initial * (2 ** attempt)) + min(JITTER_MAX, max(0.0, float(self._jitter())))
        retry_after = _retry_after(headers, observed)
        if retry_after is not None:
            delay = max(delay, retry_after)
        self._sleeper(min(RETRY_AFTER_CAP, delay))

    def _qualifying(self, kind: SecAcquisitionFailureKind) -> None:
        if kind in {SecAcquisitionFailureKind.TIMEOUT, SecAcquisitionFailureKind.CONNECTION_ERROR, SecAcquisitionFailureKind.HTTP_RATE_LIMITED, SecAcquisitionFailureKind.HTTP_SERVER_ERROR}:
            self._circuit.failure()
            self._record_attempt_metric(kind)

    def _record_attempt_metric(self, kind: SecAcquisitionFailureKind) -> None:
        field = {
            SecAcquisitionFailureKind.TIMEOUT: "timeouts",
            SecAcquisitionFailureKind.CONNECTION_ERROR: "connection_errors",
            SecAcquisitionFailureKind.HTTP_RATE_LIMITED: "rate_limited",
            SecAcquisitionFailureKind.HTTP_SERVER_ERROR: "server_errors",
        }.get(kind)
        if field is not None:
            self._inc(field)

    def _failure(self, kind: SecAcquisitionFailureKind, request: SecEdgarRequest, attempts: int, observed: datetime, status: int | None = None) -> SecAcquisitionResult:
        return SecAcquisitionResult(failure=SecAcquisitionFailure(kind, request.endpoint, observed, attempts, status))

    def _inc(self, field: str) -> None:
        with self._metrics_lock:
            data = {name: getattr(self._metrics, name) for name in self._metrics.__dataclass_fields__}
            data[field] += 1
            self._metrics = SecTransportMetrics(**data)

    def _update_success(self, size: int, latency: float) -> None:
        with self._metrics_lock:
            data = {name: getattr(self._metrics, name) for name in self._metrics.__dataclass_fields__}
            data["successful_acquisitions"] += 1
            data["bytes_received"] += size
            data["latency_seconds"] += max(0.0, latency)
            data["last_success_at"] = self._wall_clock().astimezone(UTC)
            self._metrics = SecTransportMetrics(**data)


def _retry_after(headers: tuple[tuple[str, str], ...], now: datetime) -> float | None:
    raw = next((value for key, value in headers if key == "retry-after"), None)
    if raw is None:
        return None
    try:
        value = float(raw.strip())
        return min(RETRY_AFTER_CAP, value) if math.isfinite(value) and value >= 0 else None
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            seconds = (parsed.astimezone(UTC) - now).total_seconds()
            return min(RETRY_AFTER_CAP, seconds) if seconds > 0 else None
        except (TypeError, ValueError, OverflowError):
            return None


def _bounded_content(response: object, limit: int) -> bytes | None:
    iterator = getattr(response, "iter_bytes", None)
    if callable(iterator):
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in iterator():
                if not isinstance(chunk, bytes):
                    return None
                total += len(chunk)
                if total > limit:
                    return None
                chunks.append(chunk)
        except Exception:
            return None
        return b"".join(chunks)
    content = getattr(response, "content", b"")
    return content if isinstance(content, bytes) and len(content) <= limit else None


__all__ = [
    "ALLOWED_HOSTS", "COOLDOWN_THRESHOLD", "JITTER_MAX", "RETRY_AFTER_CAP",
    "SUBMISSIONS_BODY_LIMIT", "TICKER_BODY_LIMIT", "SecAcquisitionFailure",
    "SecAcquisitionFailureKind", "SecAcquisitionResult", "SecEdgarEndpointClass",
    "SecEdgarRateLimiter", "SecEdgarRequest", "SecEdgarResponse", "SecEdgarTransport",
    "SecTransportMetrics",
]

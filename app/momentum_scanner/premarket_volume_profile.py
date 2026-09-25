"""Bounded background acquisition for research-only premarket volume profiles."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import IntEnum, StrEnum
from threading import Condition, Event, RLock, Thread
from time import perf_counter

from app.market.calendar import EASTERN
from .premarket_rvol_shadow import (
    PREMARKET_MINUTES,
    PREMARKET_PROFILE_VERSION,
    PREMARKET_START,
    REGULAR_SESSION_START,
    PremarketRvolShadow,
    PremarketRvolShadowResult,
    PremarketVolumeProfileStatus,
)


class PremarketProfilePriority(IntEnum):
    RETAINED = 0
    LEGACY_PRIMARY = 1
    ACCELERATOR = 2
    BACKGROUND = 3


class PremarketHistoryCapability(StrEnum):
    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PremarketHistoryCapabilityMetrics:
    status: PremarketHistoryCapability
    provider: str
    reason: str | None
    capability_probe_attempts: int
    capability_unavailable_latched: int
    requests_suppressed_due_to_capability: int
    profiles_unavailable: int


@dataclass(frozen=True, slots=True)
class PremarketProfileRequest:
    symbol: str
    trading_date: date
    historical_cutoff: date
    profile_version: str
    priority: PremarketProfilePriority
    sequence: int

    @property
    def identity(self) -> tuple[str, date, date, str]:
        return (
            self.symbol, self.trading_date, self.historical_cutoff,
            self.profile_version,
        )


@dataclass(frozen=True, slots=True)
class PremarketProfileWorkerMetrics:
    accepted: int
    completed: int
    insufficient: int
    failed: int
    rejected: int
    duplicate_suppressed: int
    cache_hits: int
    queue_depth: int
    queue_high_water: int


@dataclass(frozen=True, slots=True)
class PremarketProfilePrioritySnapshot:
    retained: tuple[str, ...] = ()
    legacy_primary: tuple[str, ...] = ()
    accelerator: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PremarketHistoryPage:
    bars: tuple[object, ...]
    next_cursor: object | None = None


@dataclass(frozen=True, slots=True)
class PremarketHistoryAcquisition:
    bars: tuple[object, ...]
    status: PremarketVolumeProfileStatus
    request_count: int
    failed_count: int
    session_count: int
    extended_hours_detected: bool

    def __iter__(self):
        return iter(self.bars)


class CurrentPremarketVolumeAccumulator:
    """Bounded current-session completed-M1 volume curves."""

    def __init__(self, *, maximum_symbols: int = 256) -> None:
        if maximum_symbols <= 0:
            raise ValueError("maximum_symbols must be positive")
        self._maximum_symbols = maximum_symbols
        self._states: OrderedDict[
            tuple[str, date], tuple[tuple[Decimal, ...], tuple[Decimal, ...]]
        ] = OrderedDict()
        self._lock = RLock()

    def observe(self, bar: object, *, observed_at: datetime) -> bool:
        timestamp = _aware(getattr(bar, "timestamp", None))
        local = timestamp.astimezone(EASTERN)
        now = _aware(observed_at).astimezone(EASTERN)
        volume = _volume(getattr(bar, "volume", None))
        if (
            local.date() != now.date()
            or not (PREMARKET_START <= local.time().replace(tzinfo=None) < REGULAR_SESSION_START)
            or timestamp >= now.replace(second=0, microsecond=0)
        ):
            return False
        key = (_symbol(getattr(bar, "symbol", "")), local.date())
        index = local.hour * 60 + local.minute - 240
        with self._lock:
            existing = self._states.get(key)
            values = list(existing[0]) if existing is not None else [
                Decimal("0") for _ in range(PREMARKET_MINUTES)
            ]
            values[index] = volume
            running = Decimal("0")
            cumulative = []
            for amount in values:
                running += amount
                cumulative.append(running)
            self._states[key] = (tuple(values), tuple(cumulative))
            self._states.move_to_end(key)
            while len(self._states) > self._maximum_symbols:
                self._states.popitem(last=False)
        return True

    def seed(self, symbol: str, bars: Iterable[object], *, observed_at: datetime) -> int:
        accepted = 0
        for bar in bars:
            if not getattr(bar, "symbol", None):
                try:
                    object.__setattr__(bar, "symbol", _symbol(symbol))
                except (AttributeError, TypeError):
                    bar = _SymbolBar(_symbol(symbol), bar)
            accepted += int(self.observe(bar, observed_at=observed_at))
        return accepted

    def cumulative(self, symbol: str, *, timestamp: datetime) -> Decimal:
        local = _aware(timestamp).astimezone(EASTERN)
        comparison = local.replace(second=0, microsecond=0)
        index = comparison.hour * 60 + comparison.minute - 241
        if index < 0:
            return Decimal("0")
        index = min(index, PREMARKET_MINUTES - 1)
        with self._lock:
            state = self._states.get((_symbol(symbol), local.date()))
            return Decimal("0") if state is None else state[1][index]


@dataclass(frozen=True, slots=True)
class _SymbolBar:
    symbol: str
    source: object

    def __getattr__(self, name: str) -> object:
        return getattr(self.source, name)


class PremarketProfileWorker:
    """Single bounded priority worker; never participates in authority."""

    def __init__(
        self,
        history_source: Callable[[str], Iterable[object]],
        shadow: PremarketRvolShadow,
        numerator: CurrentPremarketVolumeAccumulator,
        *,
        capacity: int = 32,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not callable(history_source):
            raise TypeError("history_source must be callable")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._source = history_source
        self._shadow = shadow
        self._numerator = numerator
        self._capacity = capacity
        self._clock = clock
        self._condition = Condition(RLock())
        self._pending: dict[
            tuple[str, date, date, str], PremarketProfileRequest
        ] = {}
        self._inflight: set[tuple[str, date, date, str]] = set()
        self._thread: Thread | None = None
        self._stopping = False
        self._sequence = 0
        self._accepted = self._completed = self._insufficient = 0
        self._failed = self._rejected = self._duplicates = 0
        self._cache_hits = self._high_water = 0
        self._build_durations_ms: list[float] = []

    def start(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = Thread(
                target=self._run, name="premarket-profile", daemon=True,
            )
            self._thread.start()

    def submit(
        self,
        symbol: str,
        *,
        priority: PremarketProfilePriority,
        as_of: datetime,
        profile_version: str = PREMARKET_PROFILE_VERSION,
    ) -> bool:
        normalized = _symbol(symbol)
        trading_date = _aware(as_of).astimezone(EASTERN).date()
        identity = (normalized, trading_date, trading_date, profile_version)
        with self._condition:
            if self._stopping:
                self._rejected += 1
                return False
        if self._shadow.cached_profile(
            normalized, trading_date=trading_date,
            profile_version=profile_version,
        ) is not None:
            with self._condition:
                self._cache_hits += 1
            return True
        if (
            getattr(self._source, "capability_status", None)
            is PremarketHistoryCapability.UNAVAILABLE
        ):
            unavailable = getattr(self._source, "suppressed_result", None)
            if callable(unavailable):
                unavailable(normalized)
            profile = self._shadow.build_profile(
                normalized, (), as_of=as_of, profile_version=profile_version,
            )
            self._shadow.install_profile(replace(
                profile,
                status=(
                    PremarketVolumeProfileStatus
                    .EXTENDED_HOURS_HISTORY_UNAVAILABLE
                ),
            ))
            with self._condition:
                self._completed += 1
                self._insufficient += 1
            return True
        with self._condition:
            if self._stopping:
                self._rejected += 1
                return False
            if identity in self._pending or identity in self._inflight:
                self._duplicates += 1
                return True
            self._sequence += 1
            request = PremarketProfileRequest(
                normalized, trading_date, trading_date, profile_version,
                PremarketProfilePriority(priority), self._sequence,
            )
            if len(self._pending) >= self._capacity:
                worst = max(
                    self._pending.values(),
                    key=lambda item: (item.priority, item.sequence),
                )
                if request.priority >= worst.priority:
                    self._rejected += 1
                    return False
                self._pending.pop(worst.identity)
                self._rejected += 1
            self._pending[identity] = request
            self._accepted += 1
            self._high_water = max(self._high_water, len(self._pending))
            self._condition.notify()
            return True

    def submit_priorities(
        self, snapshot: PremarketProfilePrioritySnapshot, *, as_of: datetime,
    ) -> None:
        seen: set[str] = set()
        for priority, symbols in (
            (PremarketProfilePriority.RETAINED, snapshot.retained),
            (PremarketProfilePriority.LEGACY_PRIMARY, snapshot.legacy_primary),
            (PremarketProfilePriority.ACCELERATOR, snapshot.accelerator),
        ):
            for symbol in symbols:
                normalized = _symbol(symbol)
                if normalized not in seen:
                    seen.add(normalized)
                    self.submit(normalized, priority=priority, as_of=as_of)

    def stop(self, *, timeout_seconds: float = 2.0) -> bool:
        with self._condition:
            self._stopping = True
            self._pending.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout_seconds))
        stopped = not thread.is_alive()
        if stopped:
            self._thread = None
        return stopped

    def metrics(self) -> PremarketProfileWorkerMetrics:
        with self._condition:
            return PremarketProfileWorkerMetrics(
                self._accepted, self._completed, self._insufficient,
                self._failed, self._rejected, self._duplicates,
                self._cache_hits, len(self._pending), self._high_water,
            )

    @property
    def build_durations_ms(self) -> tuple[float, ...]:
        with self._condition:
            return tuple(self._build_durations_ms)

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stopping:
                    self._condition.wait()
                if self._stopping:
                    return
                request = min(
                    self._pending.values(),
                    key=lambda item: (item.priority, item.sequence),
                )
                self._pending.pop(request.identity)
                self._inflight.add(request.identity)
            started = perf_counter()
            try:
                acquired = self._source(request.symbol)
                bars = tuple(acquired)
                now = _aware(self._clock())
                profile = self._shadow.build_profile(
                    request.symbol, bars, as_of=now,
                    profile_version=request.profile_version,
                )
                if isinstance(acquired, PremarketHistoryAcquisition):
                    if acquired.status is not PremarketVolumeProfileStatus.AVAILABLE:
                        profile = replace(profile, status=acquired.status)
                # A result finishing after a trading-date rollover is stale.
                if now.astimezone(EASTERN).date() == request.trading_date:
                    self._shadow.install_profile(profile)
                    self._numerator.seed(request.symbol, bars, observed_at=now)
                    with self._condition:
                        self._completed += 1
                        if profile.status is not PremarketVolumeProfileStatus.AVAILABLE:
                            self._insufficient += 1
            except Exception:
                with self._condition:
                    self._failed += 1
            finally:
                elapsed = (perf_counter() - started) * 1000.0
                with self._condition:
                    self._build_durations_ms.append(elapsed)
                    if len(self._build_durations_ms) > 1024:
                        self._build_durations_ms.pop(0)
                    self._inflight.discard(request.identity)


class ChartPremarketHistorySource:
    """Bounded progressive count windows with optional explicit cursor pages."""

    def __init__(
        self,
        service: object,
        *,
        request_counts: tuple[int, ...] = (120, 1650),
        maximum_requests: int = 12,
        minimum_sessions: int = 10,
        provider_identity: str | None = None,
        profile_version: str = PREMARKET_PROFILE_VERSION,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        telemetry_sink: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if not callable(getattr(service, "load_historical_bars_strict", None)):
            raise TypeError("service must support strict bounded history loads")
        if not request_counts or any(value <= 0 for value in request_counts):
            raise ValueError("request_counts must be positive")
        if maximum_requests <= 0 or minimum_sessions <= 0:
            raise ValueError("request budget and minimum sessions must be positive")
        self._service = service
        self._request_counts = tuple(dict.fromkeys(request_counts))
        self._maximum_requests = maximum_requests
        self._minimum_sessions = minimum_sessions
        self._provider_identity = (
            str(provider_identity).strip()
            if provider_identity is not None
            else f"{type(service).__module__}.{type(service).__qualname__}"
        )
        self._profile_version = str(profile_version)
        self._clock = clock
        self._telemetry_sink = telemetry_sink
        self._unsupported_counts: set[int] = set()
        self._capability = PremarketHistoryCapability.UNKNOWN
        self._capability_reason: str | None = None
        self._capability_probe_attempts = 0
        self._capability_unavailable_latched = 0
        self._requests_suppressed_due_to_capability = 0
        self._profiles_unavailable = 0
        self._lock = RLock()

    def __call__(self, symbol: str) -> PremarketHistoryAcquisition:
        normalized = _symbol(symbol)
        with self._lock:
            if self._capability is PremarketHistoryCapability.UNAVAILABLE:
                self._requests_suppressed_due_to_capability += 1
                self._profiles_unavailable += 1
                return self._capability_unavailable_result(normalized)
            self._capability_probe_attempts += 1
        as_of = _aware(self._clock()).astimezone(EASTERN)
        unique: dict[datetime, object] = {}
        requested = failed = 0
        any_success = False
        cursor = None
        counts = tuple(
            value for value in self._request_counts
            if not self._unsupported(value)
        )
        for count in counts:
            while requested < self._maximum_requests:
                requested += 1
                try:
                    page = self._load_page(normalized, count, cursor)
                    any_success = True
                except Exception:
                    failed += 1
                    # The live-safe 120-bar shape may fail transiently. Larger
                    # shapes are capability probes and are suppressed for the
                    # rest of this provider/runtime session after rejection.
                    if count != 120:
                        with self._lock:
                            self._unsupported_counts.add(count)
                    break
                for bar in page.bars:
                    timestamp = getattr(bar, "timestamp", None)
                    if isinstance(timestamp, datetime):
                        unique[_aware(timestamp)] = bar
                sessions = _historical_premarket_sessions(
                    unique.values(), cutoff=as_of.date(),
                )
                if len(sessions) >= self._minimum_sessions:
                    self._set_capability(PremarketHistoryCapability.AVAILABLE)
                    return self._result(
                        normalized, unique, requested, failed,
                        PremarketVolumeProfileStatus.AVAILABLE,
                    )
                if (
                    page.next_cursor is None
                    or not callable(
                        getattr(self._service, "load_historical_bars_page", None)
                    )
                ):
                    break
                cursor = page.next_cursor
            cursor = None
            if requested >= self._maximum_requests:
                break
        sessions = _historical_premarket_sessions(
            unique.values(), cutoff=as_of.date(),
        )
        if not any_success:
            status = PremarketVolumeProfileStatus.PROVIDER_UNAVAILABLE
        elif unique and not sessions:
            status = PremarketVolumeProfileStatus.EXTENDED_HOURS_HISTORY_UNAVAILABLE
            self._set_capability(
                PremarketHistoryCapability.UNAVAILABLE,
                reason="successful M1 history contained no 04:00-09:29 ET bars",
            )
        else:
            status = PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY
        if sessions:
            self._set_capability(PremarketHistoryCapability.AVAILABLE)
        return self._result(normalized, unique, requested, failed, status)

    @property
    def capability_status(self) -> PremarketHistoryCapability:
        with self._lock:
            return self._capability

    def capability_metrics(self) -> PremarketHistoryCapabilityMetrics:
        with self._lock:
            return PremarketHistoryCapabilityMetrics(
                self._capability, self._provider_identity,
                self._capability_reason, self._capability_probe_attempts,
                self._capability_unavailable_latched,
                self._requests_suppressed_due_to_capability,
                self._profiles_unavailable,
            )

    def suppressed_result(self, symbol: str) -> PremarketHistoryAcquisition:
        normalized = _symbol(symbol)
        with self._lock:
            self._requests_suppressed_due_to_capability += 1
            self._profiles_unavailable += 1
        return self._capability_unavailable_result(normalized)

    def _set_capability(
        self, status: PremarketHistoryCapability, *, reason: str | None = None,
    ) -> None:
        changed = False
        with self._lock:
            if (
                status is PremarketHistoryCapability.UNAVAILABLE
                and self._capability is not PremarketHistoryCapability.UNAVAILABLE
            ):
                self._capability_unavailable_latched += 1
                changed = True
            elif status is not self._capability:
                changed = True
            self._capability = status
            self._capability_reason = reason
        if changed:
            self._emit_capability()

    def _capability_unavailable_result(
        self, symbol: str,
    ) -> PremarketHistoryAcquisition:
        result = PremarketHistoryAcquisition(
            (), PremarketVolumeProfileStatus.EXTENDED_HOURS_HISTORY_UNAVAILABLE,
            0, 0, 0, False,
        )
        self._emit_capability(symbol=symbol)
        return result

    def _emit_capability(self, *, symbol: str | None = None) -> None:
        if self._telemetry_sink is None:
            return
        metrics = self.capability_metrics()
        try:
            self._telemetry_sink({
                "event": "profile_history_capability",
                "symbol": symbol,
                "profile_history_capability.status": metrics.status.value,
                "profile_history_capability.provider": metrics.provider,
                "profile_history_capability.reason": metrics.reason,
                "capability_probe_attempts": metrics.capability_probe_attempts,
                "capability_unavailable_latched": (
                    metrics.capability_unavailable_latched
                ),
                "requests_suppressed_due_to_capability": (
                    metrics.requests_suppressed_due_to_capability
                ),
                "profiles_unavailable": metrics.profiles_unavailable,
                "profile_version": self._profile_version,
            })
        except Exception:
            pass

    @property
    def unsupported_counts(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._unsupported_counts)

    def _unsupported(self, count: int) -> bool:
        with self._lock:
            return count in self._unsupported_counts

    def _result(
        self,
        symbol: str,
        unique: dict[datetime, object],
        requested: int,
        failed: int,
        status: PremarketVolumeProfileStatus,
    ) -> PremarketHistoryAcquisition:
        bars = tuple(value for _, value in sorted(unique.items()))
        cutoff = _aware(self._clock()).astimezone(EASTERN).date()
        sessions = _historical_premarket_sessions(bars, cutoff=cutoff)
        result = PremarketHistoryAcquisition(
            bars=bars,
            status=status,
            request_count=requested,
            failed_count=failed,
            session_count=len(sessions),
            extended_hours_detected=bool(sessions),
        )
        if self._telemetry_sink is not None:
            try:
                self._telemetry_sink({
                    "event": "profile_history",
                    "symbol": symbol,
                    "profile_history.requested": requested,
                    "profile_history.succeeded": requested - failed,
                    "profile_history.failed": failed,
                    "profile_history.chunk_count": requested,
                    "profile_history.bar_count": len(bars),
                    "profile_history.session_count": len(sessions),
                    "profile_history.extended_hours_detected": bool(sessions),
                    "profile_history.profile_ready": (
                        status is PremarketVolumeProfileStatus.AVAILABLE
                    ),
                    "profile_history.status": status.value,
                })
            except Exception:
                pass
        return result

    def _load_page(
        self, symbol: str, count: int, cursor: object | None,
    ) -> PremarketHistoryPage:
        paged = getattr(self._service, "load_historical_bars_page", None)
        if cursor is not None and callable(paged):
            value = paged(symbol, "1M", count=count, cursor=cursor)
        else:
            value = self._service.load_historical_bars_strict(
                symbol, "1M", count=count,
            )
        if isinstance(value, PremarketHistoryPage):
            return value
        return PremarketHistoryPage(tuple(value))


class PremarketVolumeProfilePipeline:
    """Non-authorizing facade used by production composition and tests."""

    def __init__(
        self,
        worker: PremarketProfileWorker,
        shadow: PremarketRvolShadow,
        numerator: CurrentPremarketVolumeAccumulator,
    ) -> None:
        self.worker = worker
        self.shadow = shadow
        self.numerator = numerator

    def start(self) -> None:
        self.worker.start()

    def close(self) -> bool:
        return self.worker.stop()

    def observe_completed_bar(self, bar: object, *, observed_at: datetime) -> bool:
        try:
            return self.numerator.observe(bar, observed_at=observed_at)
        except Exception:
            return False

    def lookup(
        self,
        symbol: str,
        *,
        timestamp: datetime,
        legacy_rvol: Decimal | None,
    ) -> PremarketRvolShadowResult:
        current = self.numerator.cumulative(symbol, timestamp=timestamp)
        return self.shadow.lookup(
            symbol, timestamp=timestamp,
            current_completed_premarket_volume=current,
            legacy_rvol=legacy_rvol,
        )


class PremarketProfileRuntime:
    """Lightweight post-start scheduler for current production priority lanes."""

    def __init__(
        self,
        pipeline: PremarketVolumeProfilePipeline,
        priority_source: Callable[[], PremarketProfilePrioritySnapshot],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        refresh_seconds: float = 30.0,
    ) -> None:
        if not callable(priority_source):
            raise TypeError("priority_source must be callable")
        if refresh_seconds <= 0:
            raise ValueError("refresh_seconds must be positive")
        self.pipeline = pipeline
        self._priority_source = priority_source
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.pipeline.start()
        self._stop.clear()
        self._thread = Thread(
            target=self._run, name="premarket-profile-scheduler", daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None
        self.pipeline.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = self._priority_source()
                if isinstance(snapshot, PremarketProfilePrioritySnapshot):
                    self.pipeline.worker.submit_priorities(
                        snapshot, as_of=self._clock(),
                    )
            except Exception:
                pass
            self._stop.wait(self._refresh_seconds)


def _symbol(value: object) -> str:
    normalized = str(value).strip().upper()
    if not normalized:
        raise ValueError("symbol is required")
    return normalized


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _volume(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("volume must be numeric") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("volume must be non-negative")
    return amount


def _historical_premarket_sessions(
    bars: Iterable[object], *, cutoff: date,
) -> frozenset[date]:
    sessions: set[date] = set()
    for bar in bars:
        try:
            local = _aware(getattr(bar, "timestamp", None)).astimezone(EASTERN)
        except (AttributeError, TypeError, ValueError):
            continue
        local_time = local.time().replace(tzinfo=None)
        if (
            local.date() < cutoff
            and PREMARKET_START <= local_time < REGULAR_SESSION_START
        ):
            sessions.add(local.date())
    return frozenset(sessions)


__all__ = [
    "ChartPremarketHistorySource",
    "CurrentPremarketVolumeAccumulator",
    "PremarketHistoryCapability",
    "PremarketHistoryCapabilityMetrics",
    "PremarketHistoryAcquisition",
    "PremarketHistoryPage",
    "PremarketProfilePriority",
    "PremarketProfilePrioritySnapshot",
    "PremarketProfileRequest",
    "PremarketProfileRuntime",
    "PremarketProfileWorker",
    "PremarketProfileWorkerMetrics",
    "PremarketVolumeProfilePipeline",
]

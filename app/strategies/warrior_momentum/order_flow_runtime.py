"""Bounded background retrieval and caching for optional Webull flow evidence."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import IntEnum, StrEnum
from threading import Condition, Event, RLock, Thread
from time import perf_counter

from app.performance_diagnostics import performance_diagnostics
from typing import Callable

from .order_flow import (
    OrderFlowAssessment, OrderFlowClassification, OrderFlowMemory,
    detect_order_flow_capabilities,
    normalize_capital_flow_response,
    normalize_footprint_response,
)


class OrderFlowPriority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class OrderFlowFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class OrderFlowPollingConfig:
    maximum_active_symbols: int = 50
    high_footprint_seconds: Decimal = Decimal("12")
    medium_footprint_seconds: Decimal = Decimal("24")
    low_footprint_seconds: Decimal = Decimal("60")
    high_capital_flow_seconds: Decimal = Decimal("45")
    medium_capital_flow_seconds: Decimal = Decimal("90")
    low_capital_flow_seconds: Decimal = Decimal("180")
    footprint_fresh_seconds: Decimal = Decimal("45")
    capital_flow_fresh_seconds: Decimal = Decimal("240")
    maximum_backoff_seconds: Decimal = Decimal("300")
    diagnostics_capacity: int = 128

    def __post_init__(self) -> None:
        if self.maximum_active_symbols <= 0 or self.diagnostics_capacity <= 0:
            raise ValueError("polling bounds must be positive")


@dataclass(frozen=True, slots=True)
class OrderFlowCacheEntry:
    symbol: str
    endpoint: str
    fetched_at: datetime | None
    observed_at: datetime | None
    freshness: OrderFlowFreshness
    error: str | None
    failure_count: int
    next_refresh_at: datetime


@dataclass(frozen=True, slots=True)
class OrderFlowDiagnostic:
    event: str
    symbol: str
    endpoint: str
    priority: OrderFlowPriority
    occurred_at: datetime
    freshness: OrderFlowFreshness
    reason: str | None = None


@dataclass(slots=True)
class _TrackedSymbol:
    priority: OrderFlowPriority
    footprint_due: datetime
    capital_due: datetime
    failure_counts: dict[str, int]


class OrderFlowPollingService:
    """Latest-only flow cache with a bounded symbol set and worker."""

    def __init__(
        self,
        client: object | None,
        *,
        config: OrderFlowPollingConfig = OrderFlowPollingConfig(),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        footprint_fetcher: Callable[[str], object] | None = None,
        capital_flow_fetcher: Callable[[str], object] | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._clock = clock
        self._footprint_fetcher = footprint_fetcher
        self._capital_flow_fetcher = capital_flow_fetcher
        self._tracked: dict[str, _TrackedSymbol] = {}
        self._footprint: dict[str, OrderFlowCacheEntry] = {}
        self._capital_flow: dict[str, OrderFlowCacheEntry] = {}
        self._memory: dict[str, OrderFlowMemory] = {}
        self._diagnostics: deque[OrderFlowDiagnostic] = deque(
            maxlen=config.diagnostics_capacity
        )
        self._lock = RLock()
        self._wake = Condition(self._lock)
        self._stop = Event()
        self._thread: Thread | None = None
        self._running = False
        self._dropped_symbols = 0

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def active_symbols(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._tracked))

    @property
    def dropped_symbols(self) -> int:
        with self._lock:
            return self._dropped_symbols

    @property
    def diagnostics(self) -> tuple[OrderFlowDiagnostic, ...]:
        with self._lock:
            return tuple(self._diagnostics)

    def start(self) -> None:
        with self._lock:
            if self._running or self._client is None:
                return
            self._stop.clear()
            self._running = True
            self._thread = Thread(
                target=self._run, name="warrior-order-flow", daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        with self._lock:
            self._running = False
            self._stop.set()
            self._wake.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)
        with self._lock:
            self._thread = None

    def update_symbol(
        self, symbol: str, priority: OrderFlowPriority,
    ) -> bool:
        normalized = symbol.strip().upper()
        if not normalized or self._client is None:
            return False
        now = self._clock()
        with self._lock:
            current = self._tracked.get(normalized)
            if current is not None:
                current.priority = priority
                self._wake.notify_all()
                return True
            if len(self._tracked) >= self._config.maximum_active_symbols:
                lowest = min(self._tracked.items(), key=lambda item: item[1].priority)
                if lowest[1].priority >= priority:
                    self._dropped_symbols += 1
                    return False
                self._tracked.pop(lowest[0])
            self._tracked[normalized] = _TrackedSymbol(
                priority, now, now, {"FOOTPRINT": 0, "CAPITAL_FLOW": 0},
            )
            self._memory.setdefault(normalized, OrderFlowMemory())
            self._diagnostics.append(OrderFlowDiagnostic(
                "ORDER_FLOW_SYMBOL_ADDED", normalized, "BOTH", priority, now,
                OrderFlowFreshness.UNAVAILABLE,
            ))
            self._wake.notify_all()
            return True

    def remove_symbol(self, symbol: str) -> None:
        normalized = symbol.strip().upper()
        with self._lock:
            tracked = self._tracked.pop(normalized, None)
            if tracked is not None:
                self._diagnostics.append(OrderFlowDiagnostic(
                    "ORDER_FLOW_SYMBOL_REMOVED", normalized, "BOTH",
                    tracked.priority, self._clock(), OrderFlowFreshness.UNAVAILABLE,
                ))

    def assessment(self, symbol: str, *, now: datetime | None = None) -> OrderFlowAssessment | None:
        normalized = symbol.strip().upper()
        with self._lock:
            memory = self._memory.get(normalized)
            if memory is None:
                return None
            evaluated_at = self._clock() if now is None else now
            cache = self._footprint.get(normalized)
            if cache is not None and cache.freshness is OrderFlowFreshness.ERROR:
                return OrderFlowAssessment(
                    OrderFlowClassification.UNAVAILABLE, evaluated_at, normalized,
                    fresh=False, reasons=("ORDER_FLOW_FETCH_FAILED",),
                )
            return memory.assess(
                evaluated_at=evaluated_at,
                maximum_age_seconds=self._config.footprint_fresh_seconds,
            )

    def cache_entry(self, symbol: str, endpoint: str) -> OrderFlowCacheEntry | None:
        normalized = symbol.strip().upper()
        with self._lock:
            source = self._footprint if endpoint == "FOOTPRINT" else self._capital_flow
            return source.get(normalized)

    def poll_once(self, *, now: datetime | None = None) -> int:
        """Run due work synchronously for the worker and deterministic tests."""
        current = self._clock() if now is None else now
        with self._lock:
            due = tuple(
                (symbol, tracked.priority, "FOOTPRINT")
                for symbol, tracked in self._tracked.items()
                if tracked.footprint_due <= current
            ) + tuple(
                (symbol, tracked.priority, "CAPITAL_FLOW")
                for symbol, tracked in self._tracked.items()
                if tracked.capital_due <= current
            )
        completed = 0
        for symbol, priority, endpoint in due:
            self._refresh(symbol, priority, endpoint, current)
            completed += 1
        return completed

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            with self._lock:
                if self._stop.is_set():
                    break
                self._wake.wait(timeout=1.0)

    def _refresh(
        self, symbol: str, priority: OrderFlowPriority, endpoint: str, now: datetime,
    ) -> None:
        started = perf_counter()
        success = False
        failure: str | None = None
        try:
            response = self._fetch(symbol, endpoint)
            if endpoint == "FOOTPRINT":
                snapshots = normalize_footprint_response(
                    response, symbol=symbol, observed_at=now,
                )
                if not snapshots:
                    raise ValueError("EMPTY_ORDER_FLOW_RESPONSE")
                with self._lock:
                    memory = self._memory.setdefault(symbol, OrderFlowMemory())
                    for snapshot in snapshots[-8:]:
                        memory.add(snapshot)
                observed_at = snapshots[-1].observed_at
            else:
                snapshots = normalize_capital_flow_response(
                    response, symbol=symbol, observed_at=now,
                )
                if not snapshots:
                    raise ValueError("EMPTY_CAPITAL_FLOW_RESPONSE")
                observed_at = snapshots[-1].observed_at
            next_refresh = now + self._interval(priority, endpoint)
            entry = OrderFlowCacheEntry(
                symbol, endpoint, now, observed_at, OrderFlowFreshness.FRESH,
                None, 0, next_refresh,
            )
            self._store_entry(symbol, endpoint, entry, priority)
            success = True
        except Exception as exc:
            failure = type(exc).__name__
            self._record_failure(symbol, endpoint, priority, now, exc)
        finally:
            performance_diagnostics.record_order_flow_result(
                endpoint,
                (perf_counter() - started) * 1000.0,
                success=success,
                failure=failure,
            )
            performance_diagnostics.record_component_duration(
                f"order_flow.{endpoint.lower()}_refresh",
                (perf_counter() - started) * 1000.0,
                symbol=symbol,
                success=success,
            )

    def _fetch(self, symbol: str, endpoint: str) -> object:
        if endpoint == "FOOTPRINT" and self._footprint_fetcher is not None:
            return self._footprint_fetcher(symbol)
        if endpoint == "CAPITAL_FLOW" and self._capital_flow_fetcher is not None:
            return self._capital_flow_fetcher(symbol)
        if self._client is None:
            raise RuntimeError("ORDER_FLOW_CLIENT_UNAVAILABLE")
        client = self._client.get() if callable(getattr(self._client, "get", None)) else self._client
        capabilities = detect_order_flow_capabilities(client)
        if endpoint == "FOOTPRINT":
            if not capabilities.footprint:
                raise LookupError("ORDER_FLOW_CAPABILITY_UNAVAILABLE")
            return client.market_data.get_footprint(
                [symbol], "US_STOCK", "M1", count=1,
                real_time_required=True, trading_sessions=["PRE", "RTH", "ATH"],
            )
        if not capabilities.capital_flow:
            raise LookupError("ORDER_FLOW_CAPABILITY_UNAVAILABLE")
        return client.fundamentals.get_capital_flow(symbol, "US_STOCK", count=1)

    def _interval(self, priority: OrderFlowPriority, endpoint: str) -> timedelta:
        prefix = "high" if priority is OrderFlowPriority.HIGH else "medium" if priority is OrderFlowPriority.MEDIUM else "low"
        seconds = getattr(self._config, f"{prefix}_{endpoint.lower()}_seconds")
        return timedelta(seconds=float(seconds))

    def _store_entry(
        self, symbol: str, endpoint: str, entry: OrderFlowCacheEntry,
        priority: OrderFlowPriority,
    ) -> None:
        with self._lock:
            target = self._footprint if endpoint == "FOOTPRINT" else self._capital_flow
            previous = target.get(symbol)
            target[symbol] = entry
            tracked = self._tracked.get(symbol)
            if tracked is None:
                return
            if endpoint == "FOOTPRINT":
                tracked.footprint_due = entry.next_refresh_at
            else:
                tracked.capital_due = entry.next_refresh_at
            if previous is not None and previous.freshness in {
                OrderFlowFreshness.ERROR, OrderFlowFreshness.UNAVAILABLE,
            }:
                event = "ORDER_FLOW_RECOVERED"
            elif previous is None or previous.freshness is not entry.freshness:
                event = f"{endpoint}_REFRESHED"
            else:
                event = None
            if event is not None:
                self._diagnostics.append(OrderFlowDiagnostic(
                    event, symbol, endpoint, priority, entry.fetched_at or self._clock(),
                    entry.freshness,
                ))

    def _record_failure(
        self, symbol: str, endpoint: str, priority: OrderFlowPriority,
        now: datetime, error: Exception,
    ) -> None:
        with self._lock:
            tracked = self._tracked.get(symbol)
            if tracked is None:
                return
            count = tracked.failure_counts[endpoint] + 1
            tracked.failure_counts[endpoint] = count
            delay = min(
                float(self._config.maximum_backoff_seconds),
                2.0 ** min(count, 8),
            )
            next_refresh = now + timedelta(seconds=delay)
            if endpoint == "FOOTPRINT":
                tracked.footprint_due = next_refresh
            else:
                tracked.capital_due = next_refresh
            error_text = str(error)
            if "CAPABILITY_UNAVAILABLE" in error_text:
                event = "ORDER_FLOW_CAPABILITY_UNAVAILABLE"
                freshness = OrderFlowFreshness.UNAVAILABLE
            else:
                event = "ORDER_FLOW_RATE_LIMITED" if "429" in error_text else "ORDER_FLOW_FETCH_FAILED"
                freshness = OrderFlowFreshness.ERROR
            entry = OrderFlowCacheEntry(
                symbol, endpoint, now, None, freshness,
                type(error).__name__, count, next_refresh,
            )
            target = self._footprint if endpoint == "FOOTPRINT" else self._capital_flow
            target[symbol] = entry
            self._diagnostics.append(OrderFlowDiagnostic(
                event, symbol, endpoint, priority, now, freshness,
                type(error).__name__,
            ))


__all__ = [
    "OrderFlowCacheEntry", "OrderFlowDiagnostic", "OrderFlowFreshness",
    "OrderFlowPollingConfig", "OrderFlowPollingService", "OrderFlowPriority",
]

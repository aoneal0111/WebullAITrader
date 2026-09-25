from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event
from time import monotonic, sleep
from zoneinfo import ZoneInfo

from app.momentum_scanner import (
    ChartPremarketHistorySource,
    CurrentPremarketVolumeAccumulator,
    PremarketHistoryCapability,
    PremarketHistoryPage,
    PremarketProfilePriority,
    PremarketProfilePrioritySnapshot,
    PremarketProfileRuntime,
    PremarketProfileWorker,
    PremarketRvolShadow,
    PremarketRvolShadowStatus,
    PremarketVolumeProfilePipeline,
    PremarketVolumeProfileStatus,
)


ET = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 9, 25, 5, 11, tzinfo=ET)


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    volume: Decimal


def history(days: int = 10, *, volume: int = 100):
    bars = []
    day = AS_OF.date() - timedelta(days=days + 4)
    while len({item.timestamp.astimezone(ET).date() for item in bars}) < days:
        if day.weekday() < 5:
            for minute in (0, 30, 70, 329):
                bars.append(Bar(
                    "TEST",
                    datetime.combine(day, datetime.min.time(), ET)
                    + timedelta(hours=4, minutes=minute),
                    Decimal(volume + minute),
                ))
        day += timedelta(days=1)
    return bars


def wait_until(predicate, timeout=1.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.005)
    return predicate()


def test_profile_is_time_aligned_premarket_only_and_current_session_excluded():
    shadow = PremarketRvolShadow()
    bars = history()
    bars.extend((
        Bar("TEST", datetime(2026, 9, 24, 9, 30, tzinfo=ET), Decimal(999999)),
        Bar("TEST", datetime(2026, 9, 24, 16, 0, tzinfo=ET), Decimal(999999)),
        Bar("TEST", datetime(2026, 9, 25, 4, 0, tzinfo=ET), Decimal(999999)),
    ))
    profile = shadow.build_profile("TEST", reversed(bars), as_of=AS_OF)
    assert profile.status is PremarketVolumeProfileStatus.AVAILABLE
    assert profile.session_count == 10
    assert profile.expected_cumulative[0] == Decimal(100)
    assert profile.expected_cumulative[69] == Decimal(230)
    assert profile.expected_cumulative[70] == Decimal(400)
    shadow.install_profile(profile)
    result = shadow.lookup(
        "TEST", timestamp=AS_OF,
        current_completed_premarket_volume=Decimal(800),
        legacy_rvol=Decimal("0.1"),
    )
    assert result.comparison_timestamp.minute == 10
    assert result.premarket_normalized_rvol == Decimal(2)


def test_duplicate_bars_collapse_and_insufficient_or_zero_is_unavailable():
    shadow = PremarketRvolShadow()
    bars = history(9)
    bars.append(bars[0])
    assert shadow.prepare("TEST", bars, as_of=AS_OF) == 9
    assert shadow.lookup(
        "TEST", timestamp=AS_OF,
        current_completed_premarket_volume=Decimal(1), legacy_rvol=None,
    ).status is PremarketRvolShadowStatus.UNAVAILABLE
    zero = PremarketRvolShadow()
    zero.prepare(
        "ZERO",
        [Bar("ZERO", item.timestamp, Decimal(0)) for item in history()],
        as_of=AS_OF,
    )
    assert zero.lookup(
        "ZERO", timestamp=AS_OF,
        current_completed_premarket_volume=Decimal(1), legacy_rvol=None,
    ).status is PremarketRvolShadowStatus.UNAVAILABLE


def test_current_numerator_accepts_only_completed_current_premarket_minutes():
    accumulator = CurrentPremarketVolumeAccumulator()
    values = (
        Bar("TEST", datetime(2026, 9, 25, 4, 0, tzinfo=ET), Decimal(10)),
        Bar("TEST", datetime(2026, 9, 25, 5, 10, tzinfo=ET), Decimal(20)),
        Bar("TEST", datetime(2026, 9, 25, 5, 11, tzinfo=ET), Decimal(999)),
        Bar("TEST", datetime(2026, 9, 25, 9, 30, tzinfo=ET), Decimal(999)),
        Bar("TEST", datetime(2026, 9, 24, 4, 0, tzinfo=ET), Decimal(999)),
    )
    assert [accumulator.observe(item, observed_at=AS_OF) for item in values] == [
        True, True, False, False, False,
    ]
    assert accumulator.cumulative("TEST", timestamp=AS_OF) == Decimal(30)


def test_worker_dedupes_reuses_cache_and_is_priority_aware():
    calls = []
    release = Event()

    def source(symbol):
        calls.append(symbol)
        if symbol == "BLOCK":
            release.wait(1)
        return history()

    shadow = PremarketRvolShadow()
    accumulator = CurrentPremarketVolumeAccumulator()
    worker = PremarketProfileWorker(source, shadow, accumulator, capacity=3, clock=lambda: AS_OF)
    worker.start()
    worker.submit("BLOCK", priority=PremarketProfilePriority.BACKGROUND, as_of=AS_OF)
    assert wait_until(lambda: calls == ["BLOCK"])
    assert worker.submit("DUP", priority=PremarketProfilePriority.LEGACY_PRIMARY, as_of=AS_OF)
    assert worker.submit("DUP", priority=PremarketProfilePriority.LEGACY_PRIMARY, as_of=AS_OF)
    assert worker.submit("LOW", priority=PremarketProfilePriority.BACKGROUND, as_of=AS_OF)
    assert worker.submit("KEEP", priority=PremarketProfilePriority.ACCELERATOR, as_of=AS_OF)
    assert worker.submit("TOP", priority=PremarketProfilePriority.RETAINED, as_of=AS_OF)
    release.set()
    assert wait_until(lambda: worker.metrics().completed == 4)
    assert calls[1] == "TOP"
    assert calls.count("DUP") == 1
    assert "LOW" not in calls
    assert worker.metrics().duplicate_suppressed == 1
    assert worker.metrics().queue_high_water == 3

    # Same-session repeat is a cache hit and performs no provider call.
    assert worker.submit("TOP", priority=PremarketProfilePriority.RETAINED, as_of=AS_OF)
    assert worker.metrics().cache_hits == 1
    assert worker.stop()


def test_worker_failure_is_contained_and_shutdown_discards_pending():
    started = Event()
    release = Event()

    def source(symbol):
        if symbol == "FAIL":
            raise RuntimeError("synthetic")
        started.set()
        release.wait(1)
        return history()

    worker = PremarketProfileWorker(
        source, PremarketRvolShadow(), CurrentPremarketVolumeAccumulator(),
        capacity=1, clock=lambda: AS_OF,
    )
    worker.start()
    worker.submit("FAIL", priority=PremarketProfilePriority.RETAINED, as_of=AS_OF)
    assert wait_until(lambda: worker.metrics().failed == 1)
    worker.submit("WAIT", priority=PremarketProfilePriority.RETAINED, as_of=AS_OF)
    assert started.wait(1)
    worker.submit("PENDING", priority=PremarketProfilePriority.ACCELERATOR, as_of=AS_OF)
    assert not worker.stop(timeout_seconds=0.01)
    release.set()
    assert wait_until(lambda: worker.stop(timeout_seconds=0.2))
    assert worker.metrics().queue_depth == 0


def test_worker_cache_identity_invalidates_on_next_trading_date():
    calls = []
    now = [AS_OF]
    worker = PremarketProfileWorker(
        lambda symbol: calls.append(symbol) or history(),
        PremarketRvolShadow(), CurrentPremarketVolumeAccumulator(),
        clock=lambda: now[0],
    )
    worker.start()
    worker.submit("TEST", priority=PremarketProfilePriority.LEGACY_PRIMARY, as_of=now[0])
    assert wait_until(lambda: worker.metrics().completed == 1)
    worker.submit("TEST", priority=PremarketProfilePriority.LEGACY_PRIMARY, as_of=now[0])
    assert worker.metrics().cache_hits == 1
    now[0] += timedelta(days=1)
    worker.submit("TEST", priority=PremarketProfilePriority.LEGACY_PRIMARY, as_of=now[0])
    assert wait_until(lambda: len(calls) == 2)
    assert worker.stop()


def test_priority_snapshot_never_schedules_background_and_preserves_lane_order():
    calls = []
    worker = PremarketProfileWorker(
        lambda symbol: calls.append(symbol) or history(),
        PremarketRvolShadow(), CurrentPremarketVolumeAccumulator(),
        clock=lambda: AS_OF,
    )
    worker.start()
    worker.submit_priorities(PremarketProfilePrioritySnapshot(
        retained=("SAFE",), legacy_primary=("GAINER", "RVOL"),
        accelerator=("FAST",),
    ), as_of=AS_OF)
    assert wait_until(lambda: len(calls) == 4)
    assert calls == ["SAFE", "GAINER", "RVOL", "FAST"]
    assert worker.stop()


def _session_pages(days=10):
    grouped = {}
    for bar in history(days):
        grouped.setdefault(bar.timestamp.date(), []).append(bar)
    return [tuple(values) for _, values in sorted(grouped.items())]


class PagedHistoryService:
    def __init__(self, pages, *, reject_oversized=True, fail_cursor=None):
        self.pages = pages
        self.reject_oversized = reject_oversized
        self.fail_cursor = fail_cursor
        self.calls = []

    def load_historical_bars_strict(self, symbol, timeframe, *, count):
        self.calls.append(("initial", count, None))
        if count == 12000 and self.reject_oversized:
            raise RuntimeError("ServerException")
        cursor = 1 if len(self.pages) > 1 else None
        return PremarketHistoryPage(tuple(self.pages[0]), cursor)

    def load_historical_bars_page(self, symbol, timeframe, *, count, cursor):
        self.calls.append(("page", count, cursor))
        if cursor == self.fail_cursor:
            raise RuntimeError("synthetic provider failure")
        next_cursor = cursor + 1 if cursor + 1 < len(self.pages) else None
        return PremarketHistoryPage(tuple(self.pages[cursor]), next_cursor)


def test_profile_build_recovers_from_oversized_request_and_caches_capability():
    service = PagedHistoryService(_session_pages())
    source = ChartPremarketHistorySource(
        service, request_counts=(12000, 120), maximum_requests=12,
        clock=lambda: AS_OF,
    )
    result = source("TEST")
    assert result.status is PremarketVolumeProfileStatus.AVAILABLE
    assert result.session_count == 10
    assert result.request_count == 11
    assert result.failed_count == 1
    assert source.unsupported_counts == frozenset({12000})

    # The unsupported request shape is not retried for another symbol.
    source("OTHER")
    assert [item for item in service.calls if item[1] == 12000] == [
        ("initial", 12000, None),
    ]


def test_no_pagination_and_partial_history_are_explicitly_insufficient():
    service = PagedHistoryService(_session_pages(3))
    source = ChartPremarketHistorySource(
        service, request_counts=(120,), maximum_requests=12,
        clock=lambda: AS_OF,
    )
    # Hide optional cursor support to model the repository's current contract.
    service.load_historical_bars_page = None
    result = source("TEST")
    assert result.status is PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY
    assert result.session_count == 1
    assert result.request_count == 1


def test_extended_hours_missing_is_not_misrepresented_as_history():
    regular = Bar(
        "TEST", datetime(2026, 9, 24, 9, 30, tzinfo=ET), Decimal(100),
    )
    service = PagedHistoryService(((regular,),))
    result = ChartPremarketHistorySource(
        service, request_counts=(120,), clock=lambda: AS_OF,
    )("TEST")
    assert result.status is (
        PremarketVolumeProfileStatus.EXTENDED_HOURS_HISTORY_UNAVAILABLE
    )
    assert not result.extended_hours_detected


def test_extended_hours_capability_latches_and_suppresses_second_symbol():
    regular = Bar(
        "TEST", datetime(2026, 9, 24, 9, 30, tzinfo=ET), Decimal(100),
    )
    service = PagedHistoryService(((regular,),))
    source = ChartPremarketHistorySource(
        service, request_counts=(120,), clock=lambda: AS_OF,
        provider_identity="WEBULL:paper:session-1",
    )
    first = source("FIRST")
    calls_after_probe = len(service.calls)
    second = source("SECOND")
    assert first.status is (
        PremarketVolumeProfileStatus.EXTENDED_HOURS_HISTORY_UNAVAILABLE
    )
    assert second.status is (
        PremarketVolumeProfileStatus.EXTENDED_HOURS_HISTORY_UNAVAILABLE
    )
    assert source.capability_status is PremarketHistoryCapability.UNAVAILABLE
    assert len(service.calls) == calls_after_probe
    metrics = source.capability_metrics()
    assert metrics.capability_probe_attempts == 1
    assert metrics.capability_unavailable_latched == 1
    assert metrics.requests_suppressed_due_to_capability == 1


def test_new_provider_runtime_generation_may_probe_again():
    regular = Bar(
        "TEST", datetime(2026, 9, 24, 9, 30, tzinfo=ET), Decimal(100),
    )
    first_service = PagedHistoryService(((regular,),))
    first = ChartPremarketHistorySource(
        first_service, request_counts=(120,), clock=lambda: AS_OF,
        provider_identity="WEBULL:paper:generation-1",
    )
    first("TEST")
    assert first.capability_status is PremarketHistoryCapability.UNAVAILABLE

    second_service = PagedHistoryService(_session_pages())
    second = ChartPremarketHistorySource(
        second_service, request_counts=(120,), clock=lambda: AS_OF,
        provider_identity="WEBULL:paper:generation-2",
    )
    assert second.capability_status is PremarketHistoryCapability.UNKNOWN
    result = second("TEST")
    assert result.status is PremarketVolumeProfileStatus.AVAILABLE
    assert second.capability_status is PremarketHistoryCapability.AVAILABLE
    assert second_service.calls


def test_worker_does_not_queue_or_call_provider_after_capability_latch():
    regular = Bar(
        "TEST", datetime(2026, 9, 24, 9, 30, tzinfo=ET), Decimal(100),
    )
    service = PagedHistoryService(((regular,),))
    source = ChartPremarketHistorySource(
        service, request_counts=(120,), clock=lambda: AS_OF,
    )
    source("PROBE")
    calls_after_probe = len(service.calls)
    shadow = PremarketRvolShadow()
    worker = PremarketProfileWorker(
        source, shadow, CurrentPremarketVolumeAccumulator(), clock=lambda: AS_OF,
    )
    worker.start()
    assert worker.submit(
        "SECOND", priority=PremarketProfilePriority.LEGACY_PRIMARY,
        as_of=AS_OF,
    )
    assert worker.metrics().queue_depth == 0
    assert len(service.calls) == calls_after_probe
    profile = shadow.cached_profile("SECOND", trading_date=AS_OF.date())
    assert profile is not None
    assert profile.status is (
        PremarketVolumeProfileStatus.EXTENDED_HOURS_HISTORY_UNAVAILABLE
    )
    assert worker.stop()


def test_request_budget_and_mid_profile_failure_are_contained():
    budgeted_service = PagedHistoryService(_session_pages())
    budgeted = ChartPremarketHistorySource(
        budgeted_service, request_counts=(120,), maximum_requests=3,
        clock=lambda: AS_OF,
    )("TEST")
    assert budgeted.status is PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY
    assert budgeted.request_count == 3
    assert budgeted.session_count == 3

    failing_service = PagedHistoryService(_session_pages(), fail_cursor=2)
    failed = ChartPremarketHistorySource(
        failing_service, request_counts=(120,), maximum_requests=12,
        clock=lambda: AS_OF,
    )("TEST")
    assert failed.status is PremarketVolumeProfileStatus.INSUFFICIENT_HISTORY
    assert failed.failed_count == 1
    assert failed.session_count == 2


def test_slow_profile_provider_does_not_block_runtime_startup():
    entered = Event()
    release = Event()

    def slow_source(symbol):
        entered.set()
        release.wait(1)
        return history()

    shadow = PremarketRvolShadow()
    accumulator = CurrentPremarketVolumeAccumulator()
    worker = PremarketProfileWorker(
        slow_source, shadow, accumulator, clock=lambda: AS_OF,
    )
    pipeline = PremarketVolumeProfilePipeline(
        worker, shadow, accumulator,
    )
    runtime = PremarketProfileRuntime(
        pipeline,
        lambda: PremarketProfilePrioritySnapshot(legacy_primary=("TEST",)),
        clock=lambda: AS_OF, refresh_seconds=60,
    )
    started = monotonic()
    runtime.start()
    elapsed = monotonic() - started
    assert elapsed < 0.1
    assert entered.wait(1)
    release.set()
    runtime.close()

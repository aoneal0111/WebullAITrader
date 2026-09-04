from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Event, enumerate as enumerate_threads
from time import monotonic, sleep
from types import SimpleNamespace

from app.dynamic_momentum_discovery import (
    DynamicMomentumDiscoveryRuntime,
    ProductionUniverseComparisonTracker,
    UniverseAdmissionObserverFanout,
    WebullBroadDiscoveryProvider,
)
from app.scanner_universe_observability import (
    UniverseAdmissionOutcome,
    UniverseAdmissionStage,
)
from app.momentum_scanner import AssetClass
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    WebullScannerUniverseProvider,
)
from tests.dynamic_momentum_discovery.helpers import NOW
from tests.dynamic_momentum_discovery.test_provider_and_breadth import (
    PagedScreener,
    Response,
    _row,
)


def _wait(predicate, timeout=2.0):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("condition was not reached")


def _runtime(tmp_path, screener, **kwargs):
    tracker = kwargs.pop("tracker", ProductionUniverseComparisonTracker())
    return DynamicMomentumDiscoveryRuntime(
        WebullBroadDiscoveryProvider(
            screener, page_size=50, maximum_breadth=100
        ),
        enabled=kwargs.pop("enabled", True),
        path=kwargs.pop("path", tmp_path / "shadow.jsonl"),
        comparison_source=tracker.stages_for,
        breadth=kwargs.pop("breadth", 100),
        refresh_seconds=kwargs.pop("refresh_seconds", 3600),
        queue_capacity=kwargs.pop("queue_capacity", 1024),
        maximum_retained_symbols=kwargs.pop("maximum_retained_symbols", 1000),
        clock=kwargs.pop("clock", lambda: NOW),
        session_source=kwargs.pop("session_source", lambda _: "REGULAR"),
        **kwargs,
    )


def test_disabled_runtime_creates_no_worker_provider_call_or_artifact(tmp_path):
    screener = PagedScreener()
    runtime = _runtime(tmp_path, screener, enabled=False)
    before = {thread.name for thread in enumerate_threads()}
    assert runtime.start() is False
    assert runtime.refresh_once() is None
    assert runtime.close()
    assert screener.calls == []
    assert not (tmp_path / "shadow.jsonl").exists()
    assert {thread.name for thread in enumerate_threads()} == before


def test_enabled_runtime_requests_only_pages_one_and_two_at_fifty(tmp_path):
    screener = PagedScreener()
    runtime = _runtime(tmp_path, screener)
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    assert runtime.close()
    assert [call[2]["page_index"] for call in screener.calls] == [1, 2, 1, 2]
    assert all(call[2]["page_size"] == 50 for call in screener.calls)
    metrics = runtime.metrics()
    assert metrics.provider_requests == 4
    assert metrics.unique_symbols == 125
    assert metrics.shadow_only_symbols == 125


def test_production_symbol_identity_is_unchanged_by_shared_shadow_reads(tmp_path):
    screener = PagedScreener()
    client = LazyOfficialDataClient(lambda: SimpleNamespace(screener=screener))
    production = WebullScannerUniverseProvider(client, clock=lambda: NOW)
    before = production.list_symbols(AssetClass.STOCK)

    runtime = DynamicMomentumDiscoveryRuntime(
        WebullBroadDiscoveryProvider(
            client.get().screener, page_size=50, maximum_breadth=100
        ),
        enabled=True,
        path=tmp_path / "shared-client-shadow.jsonl",
        comparison_source=lambda symbol: (),
        breadth=100,
        refresh_seconds=3600,
        clock=lambda: NOW,
        session_source=lambda _: "REGULAR",
    )
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    assert runtime.close()
    after = production.list_symbols(AssetClass.STOCK)

    assert after == before
    production_calls = [
        call for call in screener.calls
        if call[2]["page_index"] == 1 and call[2]["page_size"] == 50
    ]
    assert len(production_calls) >= 4


def test_production_comparison_is_point_in_time_and_source_preserving(tmp_path):
    tracker = ProductionUniverseComparisonTracker(maximum_symbols=10)
    tracker.begin_refresh(timestamp=NOW, session="REGULAR", page_size=50)
    tracker.record(
        stage=UniverseAdmissionStage.SCREENER_RETURNED,
        outcome=UniverseAdmissionOutcome.OBSERVED,
        reason="UPSTREAM_RESPONSE_ROW",
        screener_identity="DAY_GAINERS",
        raw_symbol="G0025",
    )
    tracker.record(
        stage=UniverseAdmissionStage.UNIVERSE_ADMITTED,
        outcome=UniverseAdmissionOutcome.ACCEPTED,
        reason="REFERENCE_READY",
        normalized_symbol="G0025",
    )
    runtime = _runtime(tmp_path, PagedScreener(), tracker=tracker)
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    assert runtime.close()
    text = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")
    assert '"production_comparison":"PRODUCTION_ADMITTED"' in text
    metrics = runtime.metrics()
    assert metrics.production_overlap == 1
    assert metrics.shadow_only_symbols == 124


def test_page_two_failure_degrades_shadow_only_and_next_source_continues(tmp_path):
    class PageTwoFailure(PagedScreener):
        def get_gainers_losers(self, *args, **kwargs):
            self.calls.append(("GAINERS", args, kwargs))
            if kwargs["page_index"] == 2:
                raise TimeoutError("page two unavailable")
            return self._page("G", kwargs)

    screener = PageTwoFailure()
    runtime = _runtime(tmp_path, screener)
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    assert runtime.close()
    assert runtime.metrics().provider_failures == 1
    assert [call[2]["page_index"] for call in screener.calls] == [1, 2, 1, 2]
    assert runtime.metrics().unique_symbols == 125


def test_persistence_failure_is_isolated_and_reported(tmp_path):
    class BrokenStore:
        def __init__(self, path): pass
        def append(self, value): raise OSError("disk unavailable")
        def close(self): pass

    runtime = _runtime(tmp_path, PagedScreener(), store_factory=BrokenStore)
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    _wait(lambda: runtime.metrics().persistence_failures > 0)
    assert runtime.close()
    assert runtime.metrics().persistence_failures == 125


def test_invalid_configuration_degrades_without_provider_work(tmp_path):
    screener = PagedScreener()
    runtime = _runtime(tmp_path, screener, breadth=200)
    assert runtime.start() is False
    assert screener.calls == []
    assert runtime.metrics().last_error_type == "ValueError"


def test_one_hundred_unchanged_refreshes_remain_bounded(tmp_path):
    runtime = _runtime(
        tmp_path, PagedScreener(), maximum_retained_symbols=150,
        queue_capacity=512,
    )
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    for _ in range(99):
        runtime.refresh_once()
    assert runtime.close()
    metrics = runtime.metrics()
    assert metrics.refresh_count == 100
    assert metrics.episodes_accepted == 125
    assert metrics.episodes_suppressed == 12_375
    assert metrics.retained_symbols == 125
    assert metrics.memory_state_size <= 125 * (2048 + 256)
    assert metrics.maximum_producer_latency_ms < 25


def test_high_churn_evicts_old_research_state(tmp_path):
    class ChurningScreener(PagedScreener):
        def __init__(self):
            super().__init__(total=50)
            self.generation = 0

        def _page(self, prefix, kwargs):
            start = (kwargs["page_index"] - 1) * kwargs["page_size"]
            rows = [
                _row(f"{prefix}{self.generation:02d}{index:03d}", index)
                for index in range(start, start + kwargs["page_size"])
            ]
            if prefix == "R":
                self.generation += 1
            return Response(rows, False)

    runtime = _runtime(
        tmp_path, ChurningScreener(), maximum_retained_symbols=20,
        queue_capacity=512,
    )
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    for _ in range(24):
        runtime.refresh_once()
    assert runtime.close()
    assert runtime.metrics().retained_symbols == 20
    assert runtime.metrics().memory_state_size <= 20 * (2048 + 256)


def test_imrn_rank_sixty_page_two_is_shadow_visible(tmp_path):
    class ImrnScreener(PagedScreener):
        def _page(self, prefix, kwargs):
            response = super()._page(prefix, kwargs)
            rows = list(response.rows)
            if prefix == "G" and kwargs["page_index"] == 2:
                rows[9] = _row("IMRN", 60)
            return Response(rows, response.has_more)

    runtime = _runtime(tmp_path, ImrnScreener())
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    assert runtime.close()
    text = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")
    assert '"symbol":"IMRN"' in text
    assert '"page_index":2' in text
    assert '"production_comparison":"PRODUCTION_NOT_RETURNED"' in text


def test_fanout_discards_observer_failures_and_return_values():
    class Primary:
        def begin_refresh(self, **values): raise RuntimeError("optional sink")
        def record(self, **values): return "must not be consumed"
        def close(self, **values): return True

    tracker = ProductionUniverseComparisonTracker()
    fanout = UniverseAdmissionObserverFanout(Primary(), tracker)
    fanout.begin_refresh(timestamp=NOW, session="REGULAR", page_size=50)
    fanout.record(
        stage=UniverseAdmissionStage.SCREENER_RETURNED,
        outcome=UniverseAdmissionOutcome.OBSERVED,
        reason="ROW", raw_symbol="IMRN", screener_identity="DAY_GAINERS",
    )
    assert tracker.stages_for("IMRN") == ("DAY_GAINERS", "SCREENER_RETURNED")
    assert fanout.close()


def test_repeated_start_close_does_not_leak_runtime_threads(tmp_path):
    before = sum(
        thread.name.startswith("atlas-dynamic-momentum")
        for thread in enumerate_threads()
    )
    for index in range(5):
        runtime = _runtime(tmp_path, PagedScreener(), path=tmp_path / f"{index}.jsonl")
        assert runtime.start()
        _wait(lambda: runtime.metrics().refresh_count == 1)
        assert runtime.close()
    _wait(lambda: sum(
        thread.name.startswith("atlas-dynamic-momentum")
        for thread in enumerate_threads()
    ) == before)


def test_blocked_provider_cannot_make_shutdown_wait_indefinitely(tmp_path):
    entered = Event()
    release = Event()

    class BlockedScreener(PagedScreener):
        def get_gainers_losers(self, *args, **kwargs):
            entered.set()
            release.wait(1)
            return super().get_gainers_losers(*args, **kwargs)

    runtime = _runtime(tmp_path, BlockedScreener())
    assert runtime.start()
    assert entered.wait(1)
    started = monotonic()
    assert runtime.close(timeout_seconds=0.05) is False
    assert monotonic() - started < 0.25
    release.set()
    _wait(lambda: not runtime.metrics().running)


def test_refresh_inputs_and_outcomes_remain_at_or_after_cutoff(tmp_path):
    runtime = _runtime(tmp_path, PagedScreener())
    assert runtime.start()
    _wait(lambda: runtime.metrics().refresh_count == 1)
    assert runtime.close()
    assert "forward_5m" not in (tmp_path / "shadow.jsonl").read_text(encoding="utf-8")

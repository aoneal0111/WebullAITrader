from __future__ import annotations

from datetime import date
from pathlib import Path
from time import perf_counter, sleep

from app.gui.presenters.application_state_presenter import PresentationCoordinator
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.report_worker import WarriorReportWorker


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def test_cpu_bound_report_work_does_not_create_unbounded_gui_heartbeat_gaps(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "report-pressure.sqlite3")

    def cpu_report(_store, _day: date, *, configuration_fingerprint=None):
        # Representative Python materialization/grouping work: deliberately
        # CPU-bound rather than sleeping in a system wait.
        total = 0
        for value in range(8_000_000):
            total = (total + value * 17) % 1_000_003
        return total

    worker = WarriorReportWorker(
        store,
        report_sink=lambda _report: None,
        failure_sink=lambda error: (_ for _ in ()).throw(error),
        builder=cpu_report,
    )
    try:
        for _ in range(64):
            worker.request_refresh(
                date(2026, 9, 23), configuration_fingerprint="test",
            )
        heartbeat_gaps: list[float] = []
        previous = perf_counter()
        deadline = previous + 2.0
        while perf_counter() < deadline:
            sleep(0.01)
            current = perf_counter()
            heartbeat_gaps.append((current - previous) * 1000.0)
            previous = current
        metrics = worker.metrics()
        print(
            "P6.1 report pressure: "
            f"heartbeat_p50_ms={_percentile(heartbeat_gaps, .50):.3f} "
            f"heartbeat_p90_ms={_percentile(heartbeat_gaps, .90):.3f} "
            f"heartbeat_p99_ms={_percentile(heartbeat_gaps, .99):.3f} "
            f"heartbeat_max_ms={max(heartbeat_gaps):.3f} "
            f"report_requests={metrics.requests} "
            f"coalesced={metrics.coalesced_requests} "
            f"builds={metrics.build_count}",
        )
        assert max(heartbeat_gaps) < 5_000.0
        assert metrics.coalesced_requests > 0
        assert metrics.build_count <= metrics.requests
    finally:
        worker.close(timeout_seconds=2.0)


def test_route_filtering_keeps_hidden_pages_out_of_refresh_work() -> None:
    calls: list[str] = []

    class Presenter:
        def __init__(self, name: str) -> None:
            self.name = name

        def render(self, _state) -> None:
            calls.append(self.name)

    dashboard = Presenter("DashboardPresenter")
    timeline = Presenter("TimelinePresenter")
    decisions = Presenter("DecisionsPresenter")
    watchlist = Presenter("WatchlistPresenter")
    health = Presenter("HealthPresenter")
    coordinator = PresentationCoordinator(
        (dashboard, timeline, decisions, watchlist, health),
        active_routes=({0}, {5}, {6}, {7}, None),
    )

    coordinator.render(object(), active_route=0)
    assert calls == ["DashboardPresenter", "HealthPresenter"]
    calls.clear()
    coordinator.render(object(), active_route=5)
    assert calls == ["TimelinePresenter", "HealthPresenter"]
    calls.clear()
    coordinator.render(object(), active_route=7)
    assert calls == ["WatchlistPresenter", "HealthPresenter"]


def test_report_shutdown_is_bounded_after_pending_requests(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "shutdown-pressure.sqlite3")

    def report(_store, _day: date, *, configuration_fingerprint=None):
        total = 0
        for value in range(500_000):
            total ^= value
        return total

    worker = WarriorReportWorker(
        store,
        report_sink=lambda _report: None,
        failure_sink=lambda _error: None,
        builder=report,
    )
    for _ in range(128):
        worker.request_refresh(date(2026, 9, 23), configuration_fingerprint="x")
    started = perf_counter()
    stopped = worker.close(timeout_seconds=2.0)
    elapsed = perf_counter() - started
    print(f"P6.1 report shutdown: stopped={stopped} seconds={elapsed:.3f}")
    assert elapsed < 2.5

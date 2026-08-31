from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event

from app.strategies.warrior_momentum.forward_models import (
    CaptureRecord,
    CaptureRecordType,
)
from app.strategies.warrior_momentum.forward_report import build_daily_report
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.report_worker import WarriorReportWorker


NOW = datetime(2026, 8, 31, 17, 30, tzinfo=UTC)


def _record(record_type: CaptureRecordType, sequence: int) -> CaptureRecord:
    payload = (
        {"stocks_in_play": ["HIGH_RELATIVE_VOLUME"]}
        if record_type is CaptureRecordType.DISCOVERY
        else {
            "to": "ENTRY_BLOCKED",
            "blocking_gates": [{"gate": "spread", "passed": False}],
        }
    )
    return CaptureRecord.create(
        record_type,
        "XYZ",
        NOW + timedelta(milliseconds=sequence),
        payload,
        identity_parts=(str(sequence),),
    )


def test_worker_report_is_equivalent_and_persists_same_report(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "forward.sqlite3")
    store.append_batch((
        _record(CaptureRecordType.DISCOVERY, 1),
        _record(CaptureRecordType.STATE_TRANSITION, 2),
    ))
    expected = build_daily_report(store, date(2026, 8, 31))
    reports = []
    failures = []
    worker = WarriorReportWorker(
        store, report_sink=reports.append, failure_sink=failures.append,
    )
    worker.request_refresh(
        date(2026, 8, 31), configuration_fingerprint=None, persist=True,
    )
    assert worker.close(timeout_seconds=2.0)
    assert failures == []
    assert reports == [expected]
    persisted = store.records(record_type=CaptureRecordType.DAILY_REPORT)
    assert len(persisted) == 1
    assert persisted[0].payload["funnel"] == [list(item) for item in expected.funnel]


def test_worker_coalesces_burst_and_stops_cleanly(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "coalesced.sqlite3")
    entered = Event()
    release = Event()
    built_dates = []

    def slow_builder(store, trading_date, *, configuration_fingerprint=None):
        entered.set()
        release.wait(2.0)
        built_dates.append(trading_date)
        return build_daily_report(
            store, trading_date,
            configuration_fingerprint=configuration_fingerprint,
        )

    worker = WarriorReportWorker(
        store,
        report_sink=lambda report: None,
        failure_sink=lambda error: None,
        builder=slow_builder,
    )
    worker.request_refresh(date(2026, 8, 1), configuration_fingerprint=None)
    assert entered.wait(1.0)
    for day in range(2, 31):
        worker.request_refresh(date(2026, 8, day), configuration_fingerprint=None)
    release.set()
    assert worker.close(timeout_seconds=2.0)
    metrics = worker.metrics()
    assert metrics.requests == 30
    assert metrics.coalesced_requests >= 28
    assert metrics.completed <= 2
    assert built_dates[-1] == date(2026, 8, 30)
    assert not worker.thread.is_alive()


def test_worker_failure_is_observable_and_does_not_escape(tmp_path: Path) -> None:
    store = ForwardCaptureStore(tmp_path / "failure.sqlite3")
    failures = []

    def fail(*args, **kwargs):
        raise OSError("report database unavailable")

    worker = WarriorReportWorker(
        store,
        report_sink=lambda report: None,
        failure_sink=failures.append,
        builder=fail,
    )
    worker.request_refresh(date(2026, 8, 31), configuration_fingerprint=None)
    assert worker.close(timeout_seconds=2.0)
    assert len(failures) == 1
    assert isinstance(failures[0], OSError)
    assert worker.metrics().failures == 1


def test_worker_timeout_is_bounded_and_thread_finishes_after_work_releases(
    tmp_path: Path,
) -> None:
    store = ForwardCaptureStore(tmp_path / "timeout.sqlite3")
    entered = Event()
    release = Event()

    def blocked(store, trading_date, *, configuration_fingerprint=None):
        entered.set()
        release.wait(2.0)
        return build_daily_report(store, trading_date)

    failures = []
    worker = WarriorReportWorker(
        store,
        report_sink=lambda report: None,
        failure_sink=failures.append,
        builder=blocked,
    )
    worker.request_refresh(date(2026, 8, 31), configuration_fingerprint=None)
    assert entered.wait(1.0)
    assert not worker.close(timeout_seconds=0.001)
    assert isinstance(failures[-1], TimeoutError)
    release.set()
    assert worker.close(timeout_seconds=2.0)
    assert not worker.thread.is_alive()

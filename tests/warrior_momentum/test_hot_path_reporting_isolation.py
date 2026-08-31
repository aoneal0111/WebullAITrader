from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, current_thread

import pytest

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload, TradePayload
from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.scanner_adapter import (
    MarketEventScannerAdapter,
    ScannerReferenceData,
    ScannerReferenceStore,
)
from app.strategies.warrior_momentum.desktop_sidecar import (
    WarriorCaptureHealth,
    WarriorDesktopSidecar,
)
from app.strategies.warrior_momentum.forward_models import (
    CaptureRecord,
    CaptureRecordType,
)
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.report_worker import (
    ReportWorkerMetrics,
    WarriorReportWorker,
)


NOW = datetime(2026, 8, 31, 17, 30, tzinfo=UTC)
D = Decimal


def _adapter() -> MarketEventScannerAdapter:
    reference = ScannerReferenceData(
        "XYZ", D("1"), D("100000"), D("6000000"),
        CatalystType.EARNINGS, "Earnings", True, NOW,
        CatalystStatus.TRUE, D("1000000"),
    )
    return MarketEventScannerAdapter(ScannerReferenceStore((reference,)))


def _quote(sequence: int, at: datetime) -> MarketEvent:
    return MarketEvent(
        sequence, at, "XYZ", "test", MarketEventType.QUOTE,
        QuotePayload(D("1.24"), D("1.26"), D("100"), D("100")),
    )


def _trade(sequence: int, at: datetime) -> MarketEvent:
    return MarketEvent(
        sequence, at, "XYZ", "test", MarketEventType.TRADE,
        TradePayload(D("1.25"), D("100"), f"trade-{sequence}"),
    )


def _deliver(scanner, sidecar, event) -> None:
    scanner.consume(event)
    sidecar(event)


class _RequestOnlyWorker:
    def __init__(self, *args, **kwargs) -> None:
        self.requests = []
        self.closed = False

    def request_refresh(self, trading_date, **kwargs):
        self.requests.append((trading_date, kwargs))
        return len(self.requests)

    def close(self, *, timeout_seconds=5.0):
        self.closed = True
        return True

    def metrics(self):
        return ReportWorkerMetrics(
            requests=len(self.requests), stopped=self.closed,
        )


@pytest.mark.parametrize("history_size", (0, 100, 1000))
def test_completed_bar_market_path_never_traverses_report_history(
    tmp_path: Path, history_size: int,
) -> None:
    path = tmp_path / f"history-{history_size}.sqlite3"
    store = ForwardCaptureStore(path)
    records = tuple(
        CaptureRecord.create(
            CaptureRecordType.DISCOVERY,
            f"S{index}",
            NOW - timedelta(minutes=1),
            {"stocks_in_play": []},
            identity_parts=(str(index),),
        )
        for index in range(history_size)
    )
    store.append_batch(records)
    scanner = _adapter()
    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=path,
        clock=lambda: NOW,
        report_worker_factory=_RequestOnlyWorker,
    )
    sidecar.bind_scanner_adapter(scanner)
    sidecar.start("TEST")
    worker = sidecar._report_worker
    assert isinstance(worker, _RequestOnlyWorker)
    original_records = sidecar._store.records
    market_thread = current_thread()

    def reject_market_thread_traversal(*args, **kwargs):
        if current_thread() is market_thread:
            raise AssertionError("full-history traversal reached market consumer")
        return original_records(*args, **kwargs)

    sidecar._store.records = reject_market_thread_traversal
    try:
        _deliver(scanner, sidecar, _quote(1, NOW))
        _deliver(scanner, sidecar, _trade(2, NOW + timedelta(seconds=1)))
        _deliver(scanner, sidecar, _trade(3, NOW + timedelta(minutes=1)))
        assert sidecar.snapshot().health is WarriorCaptureHealth.RUNNING
        assert len(worker.requests) >= 2
    finally:
        sidecar._store.records = original_records
        sidecar.stop()


def test_slow_report_build_coalesces_while_market_processing_continues(
    tmp_path: Path,
) -> None:
    entered = Event()
    release = Event()

    def factory(store, *, report_sink, failure_sink):
        from app.strategies.warrior_momentum.forward_report import build_daily_report

        def slow_builder(store, trading_date, *, configuration_fingerprint=None):
            entered.set()
            release.wait(2.0)
            return build_daily_report(
                store, trading_date,
                configuration_fingerprint=configuration_fingerprint,
            )

        return WarriorReportWorker(
            store,
            report_sink=report_sink,
            failure_sink=failure_sink,
            builder=slow_builder,
        )

    scanner = _adapter()
    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=tmp_path / "burst.sqlite3",
        clock=lambda: NOW,
        report_worker_factory=factory,
    )
    sidecar.bind_scanner_adapter(scanner)
    sidecar.start("TEST")
    assert entered.wait(1.0)
    try:
        _deliver(scanner, sidecar, _quote(1, NOW))
        for sequence in range(2, 80):
            minute = (sequence - 2) // 20
            second = (sequence - 2) % 20
            _deliver(
                scanner,
                sidecar,
                _trade(sequence, NOW + timedelta(minutes=minute, seconds=second)),
            )
        assert sidecar.snapshot().health is WarriorCaptureHealth.RUNNING
        metrics = sidecar._report_worker.metrics()
        assert metrics.requests >= 3
        assert metrics.coalesced_requests >= 1
    finally:
        release.set()
        sidecar.stop()
    assert sidecar._last_report_metrics is not None
    assert sidecar._last_report_metrics.stopped


def test_report_failure_leaves_warrior_running_and_non_authoritative(
    tmp_path: Path,
) -> None:
    failed = Event()

    def factory(store, *, report_sink, failure_sink):
        def fail(*args, **kwargs):
            failed.set()
            raise OSError("synthetic report failure")

        return WarriorReportWorker(
            store,
            report_sink=report_sink,
            failure_sink=failure_sink,
            builder=fail,
        )

    scanner = _adapter()
    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=tmp_path / "report-failure.sqlite3",
        clock=lambda: NOW,
        report_worker_factory=factory,
    )
    sidecar.bind_scanner_adapter(scanner)
    sidecar.start("TEST")
    assert failed.wait(1.0)
    _deliver(scanner, sidecar, _quote(1, NOW))
    _deliver(scanner, sidecar, _trade(2, NOW + timedelta(seconds=1)))
    assert sidecar.snapshot().health is WarriorCaptureHealth.RUNNING
    assert sidecar.snapshot().last_error_type == "REPORT:OSError"
    assert sidecar.retained_symbols() == ()
    sidecar.stop()
    assert sidecar._last_report_metrics is not None
    assert sidecar._last_report_metrics.failures >= 1


def test_sparse_latency_and_queue_diagnostics_use_existing_capture_writer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "diagnostics.sqlite3"
    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=path,
        clock=lambda: NOW,
        report_worker_factory=_RequestOnlyWorker,
    )
    sidecar.start("TEST")
    sidecar._persist_latency_diagnostic("market_latency_abnormal", {
        "recorded_at": NOW.isoformat(),
        "source": "WEBULL",
        "sequence": 42,
        "symbol": "XYZ",
        "event_type": "TRADE",
        "execution_safety": {"entry_authorized": False},
    })
    sidecar._persist_latency_diagnostic("callback_queue_threshold", {
        "recorded_at": NOW.isoformat(),
        "threshold": 1000,
        "direction": "CROSSED_UP",
        "queue_depth": 1000,
        "callback_queue_high_water": 1000,
    })
    sidecar._writer.flush()
    store = ForwardCaptureStore(path)
    assert len(store.records(record_type=CaptureRecordType.LATENCY_DIAGNOSTIC)) == 1
    assert len(store.records(record_type=CaptureRecordType.CALLBACK_QUEUE_THRESHOLD)) == 1
    latency = store.records(record_type=CaptureRecordType.LATENCY_DIAGNOSTIC)[0]
    threshold = store.records(record_type=CaptureRecordType.CALLBACK_QUEUE_THRESHOLD)[0]
    assert latency.payload["diagnostic_kind"] == "market_latency_abnormal"
    assert threshold.payload["diagnostic_kind"] == "callback_queue_threshold"
    sidecar.stop()

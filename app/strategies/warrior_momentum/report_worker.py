"""Coalesced off-hot-path Warrior daily-report refresh worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from threading import Condition, Thread
from time import perf_counter
from typing import Callable

from app.performance_diagnostics import performance_diagnostics

from .forward_report import (
    DailyForwardReport,
    build_daily_report,
    persist_daily_report,
)
from .forward_store import ForwardCaptureStore


_LOGGER = logging.getLogger("atlas.warrior.report")


@dataclass(frozen=True, slots=True)
class ReportWorkerMetrics:
    requests: int = 0
    coalesced_requests: int = 0
    completed: int = 0
    failures: int = 0
    latest_generation: int = 0
    completed_generation: int = 0
    busy: bool = False
    pending: bool = False
    stopped: bool = False
    build_count: int = 0
    last_build_duration_milliseconds: int = 0
    maximum_build_duration_milliseconds: int = 0
    last_records_materialized: int = 0
    maximum_records_materialized: int = 0


ReportBuilder = Callable[..., DailyForwardReport]
ReportPersister = Callable[[ForwardCaptureStore, DailyForwardReport], tuple[int, int]]
ReportSink = Callable[[DailyForwardReport], None]
FailureSink = Callable[[BaseException], None]


class WarriorReportWorker:
    """Own one coalesced report refresh without execution dependencies."""

    def __init__(
        self,
        store: ForwardCaptureStore,
        *,
        report_sink: ReportSink,
        failure_sink: FailureSink,
        builder: ReportBuilder = build_daily_report,
        persister: ReportPersister = persist_daily_report,
    ) -> None:
        self._store = store
        self._report_sink = report_sink
        self._failure_sink = failure_sink
        self._builder = builder
        self._persister = persister
        self._condition = Condition()
        self._pending: tuple[int, date, str | None, bool] | None = None
        self._stopping = False
        self._stopped = False
        self._busy = False
        self._requests = 0
        self._coalesced = 0
        self._completed = 0
        self._failures = 0
        self._generation = 0
        self._completed_generation = 0
        self._build_count = 0
        self._last_build_duration_milliseconds = 0
        self._maximum_build_duration_milliseconds = 0
        self._last_records_materialized = 0
        self._maximum_records_materialized = 0
        self._thread = Thread(
            target=self._run,
            name="warrior-report-refresh",
            daemon=True,
        )
        self._thread.start()

    def request_refresh(
        self,
        trading_date: date,
        *,
        configuration_fingerprint: str | None,
        persist: bool = False,
    ) -> int:
        with self._condition:
            if self._stopping:
                return self._generation
            self._requests += 1
            self._generation += 1
            if self._pending is not None:
                persist = persist or self._pending[3]
                self._coalesced += 1
            self._pending = (
                self._generation,
                trading_date,
                configuration_fingerprint,
                persist,
            )
            self._condition.notify()
            return self._generation

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        if timeout_seconds < 0:
            raise ValueError("report worker timeout cannot be negative")
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        self._thread.join(timeout_seconds)
        stopped = not self._thread.is_alive()
        with self._condition:
            self._stopped = stopped
        if not stopped:
            error = TimeoutError("Warrior report worker shutdown timed out")
            self._record_failure(error)
        return stopped

    def metrics(self) -> ReportWorkerMetrics:
        with self._condition:
            return ReportWorkerMetrics(
                requests=self._requests,
                coalesced_requests=self._coalesced,
                completed=self._completed,
                failures=self._failures,
                latest_generation=self._generation,
                completed_generation=self._completed_generation,
                busy=self._busy,
                pending=self._pending is not None,
                stopped=self._stopped,
                build_count=self._build_count,
                last_build_duration_milliseconds=(
                    self._last_build_duration_milliseconds
                ),
                maximum_build_duration_milliseconds=(
                    self._maximum_build_duration_milliseconds
                ),
                last_records_materialized=self._last_records_materialized,
                maximum_records_materialized=self._maximum_records_materialized,
            )

    def memory_metrics(self) -> dict[str, int]:
        metrics = self.metrics()
        return {
            "report_request_count": metrics.requests,
            "report_coalesced_request_count": metrics.coalesced_requests,
            "report_build_count": metrics.build_count,
            "report_generation_count": metrics.latest_generation,
            "report_completed_generation": metrics.completed_generation,
            "report_last_build_duration_milliseconds": (
                metrics.last_build_duration_milliseconds
            ),
            "report_maximum_build_duration_milliseconds": (
                metrics.maximum_build_duration_milliseconds
            ),
            "report_last_records_materialized": metrics.last_records_materialized,
            "report_maximum_records_materialized": (
                metrics.maximum_records_materialized
            ),
            "report_build_active": int(metrics.busy),
            "report_request_pending": int(metrics.pending),
            "report_failure_count": metrics.failures,
        }

    @property
    def thread(self) -> Thread:
        return self._thread

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._stopping:
                    self._condition.wait()
                if self._pending is None and self._stopping:
                    self._stopped = True
                    return
                request = self._pending
                self._pending = None
                self._busy = True
            assert request is not None
            generation, trading_date, fingerprint, persist = request
            started = perf_counter()
            materialized_before = self._store.records_materialized_total()
            report = None
            try:
                report = self._builder(
                    self._store,
                    trading_date,
                    configuration_fingerprint=fingerprint,
                )
                if persist:
                    self._persister(self._store, report)
                self._report_sink(report)
                with self._condition:
                    self._completed += 1
                    self._completed_generation = generation
            except BaseException as error:
                self._record_failure(error)
            finally:
                duration_ms = (perf_counter() - started) * 1000.0
                materialized = max(
                    0,
                    self._store.records_materialized_total()
                    - materialized_before,
                )
                performance_diagnostics.record_report_build_duration(duration_ms)
                with self._condition:
                    rounded_duration = max(0, round(duration_ms))
                    self._build_count += 1
                    self._last_build_duration_milliseconds = rounded_duration
                    self._maximum_build_duration_milliseconds = max(
                        self._maximum_build_duration_milliseconds,
                        rounded_duration,
                    )
                    self._last_records_materialized = materialized
                    self._maximum_records_materialized = max(
                        self._maximum_records_materialized,
                        materialized,
                    )
                    self._busy = False
                # The worker retains only primitive diagnostics.  Do not leave
                # the completed report in this long-lived thread frame.
                del report

    def _record_failure(self, error: BaseException) -> None:
        with self._condition:
            self._failures += 1
        performance_diagnostics.increment("report_refresh_failures")
        performance_diagnostics.emit_runtime_diagnostic(
            "report_refresh_failure",
            {"error_type": type(error).__name__},
        )
        try:
            self._failure_sink(error)
        except Exception:
            pass
        _LOGGER.error(
            "event_type=warrior_report_refresh_failed error_type=%s",
            type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )


__all__ = ["ReportWorkerMetrics", "WarriorReportWorker"]

"""Bounded background writer; strategy-critical records fail closed on pressure."""

from __future__ import annotations

from decimal import Decimal
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic, perf_counter

from .forward_models import CaptureMetrics, CaptureRecord
from .forward_store import ForwardCaptureStore


class CaptureBackpressureError(RuntimeError):
    pass


class CaptureWriterError(RuntimeError):
    pass


class ForwardCaptureWriter:
    def __init__(
        self, store: ForwardCaptureStore, *, capacity: int = 4096,
        batch_size: int = 128, flush_interval_seconds: float = 0.25,
    ) -> None:
        if capacity <= 0 or batch_size <= 0 or flush_interval_seconds <= 0:
            raise ValueError("capture writer settings must be positive")
        self._store = store
        self._queue: Queue[CaptureRecord] = Queue(maxsize=capacity)
        # Sparse latency/queue diagnostics must never inherit the critical
        # capture lane's synchronous SQLite fallback on a market thread.
        self._diagnostic_queue: Queue[CaptureRecord] = Queue(maxsize=128)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._stop = Event()
        self._lock = Lock()
        self._written = self._batches = self._duplicates = self._dropped = 0
        self._synchronous_fallback = 0
        self._latency_total = self._latency_max = 0.0
        self._gui_refreshes = 0
        self._started_at = monotonic()
        self._fatal: BaseException | None = None
        self._thread = Thread(target=self._run, name="warrior-forward-capture", daemon=True)
        self._thread.start()

    def submit(self, record: CaptureRecord, *, timeout_seconds: float = 1.0) -> None:
        if self._fatal is not None:
            raise CaptureWriterError("capture writer failed") from self._fatal
        try:
            self._queue.put(record, timeout=timeout_seconds)
        except Full as exc:
            # Critical evidence is never discarded. Apply synchronous
            # backpressure and durably append the record on the producer thread.
            try:
                inserted, duplicates = self._store.append_batch((record,))
            except BaseException as failure:
                self._fatal = failure
                raise CaptureWriterError("capture fallback write failed") from failure
            with self._lock:
                self._written += inserted
                self._duplicates += duplicates
                self._synchronous_fallback += inserted

    def submit_many(self, records: tuple[CaptureRecord, ...], *, timeout_seconds: float = 1.0) -> None:
        for record in records:
            self.submit(record, timeout_seconds=timeout_seconds)

    def submit_diagnostic(self, record: CaptureRecord) -> bool:
        """Enqueue sparse observability evidence without blocking its caller."""
        if self._fatal is not None:
            return False
        try:
            self._diagnostic_queue.put_nowait(record)
        except Full:
            with self._lock:
                self._dropped += 1
            return False
        return True

    def flush(self) -> None:
        self._queue.join()
        self._diagnostic_queue.join()
        if self._fatal is not None:
            raise CaptureWriterError("capture writer failed") from self._fatal

    def close(self) -> None:
        self.flush()
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._flush_interval * 4))
        if self._thread.is_alive():
            raise CaptureWriterError("capture writer did not stop")

    def record_gui_refresh(self) -> None:
        with self._lock:
            self._gui_refreshes += 1

    def metrics(self) -> CaptureMetrics:
        with self._lock:
            average = 0.0 if self._batches == 0 else self._latency_total / self._batches
            elapsed = max(monotonic() - self._started_at, 1e-9)
            return CaptureMetrics(
                queue_depth=self._queue.qsize(), records_written=self._written,
                batches_written=self._batches,
                average_write_latency_ms=Decimal(str(average * 1000)),
                maximum_write_latency_ms=Decimal(str(self._latency_max * 1000)),
                dropped_records=self._dropped, duplicate_records=self._duplicates,
                gui_refresh_count=self._gui_refreshes,
                gui_refresh_frequency_hz=Decimal(str(self._gui_refreshes / elapsed)),
                synchronous_fallback_records=self._synchronous_fallback,
            )

    @property
    def healthy(self) -> bool:
        return self._fatal is None

    def _run(self) -> None:
        while (
            not self._stop.is_set()
            or not self._queue.empty()
            or not self._diagnostic_queue.empty()
        ):
            try:
                first, source = self._next_record()
            except Empty:
                continue
            batch = [(first, source)]
            while len(batch) < self._batch_size:
                try:
                    record, record_source = self._next_record(nowait=True)
                    batch.append((record, record_source))
                except Empty:
                    break
            started = perf_counter()
            try:
                inserted, duplicates = self._store.append_batch(
                    tuple(record for record, _source in batch)
                )
                latency = perf_counter() - started
                with self._lock:
                    self._written += inserted
                    self._duplicates += duplicates
                    self._batches += 1
                    self._latency_total += latency
                    self._latency_max = max(self._latency_max, latency)
            except BaseException as exc:
                self._fatal = exc
            finally:
                for _record, record_source in batch:
                    record_source.task_done()

    def _next_record(
        self, *, nowait: bool = False
    ) -> tuple[CaptureRecord, Queue[CaptureRecord]]:
        try:
            return self._diagnostic_queue.get_nowait(), self._diagnostic_queue
        except Empty:
            if nowait:
                return self._queue.get_nowait(), self._queue
            return self._queue.get(timeout=self._flush_interval), self._queue


__all__ = [
    "CaptureBackpressureError", "CaptureWriterError", "ForwardCaptureWriter",
]

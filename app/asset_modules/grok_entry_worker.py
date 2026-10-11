"""One bounded background equity AI review; no desktop activation or queue."""
import json
from itertools import islice
from threading import Event, Lock, Thread, current_thread

from app.asset_modules.grok_paper_entries import EntryDispatch, GrokPaperEntries, PreparedEntry


class GrokEntryWorker:
    """Trusted adapter submits prepared placements, runtime polls completion.

    One batch is allowed, including an unread completion. Busy work is refused,
    never queued. Stop revokes subsequent dispatch checks, but cannot roll back
    an order already entering the trading service. No AI call holds this lock.
    """

    def __init__(self, dispatcher):
        if not isinstance(dispatcher, GrokPaperEntries):
            raise TypeError("Controlled Grok PAPER dispatcher required")
        self.dispatcher = dispatcher
        self._lock = Lock()
        self._stop = Event()
        self._thread = None
        self._report = None
        self._busy = False
        self._closed = False

    def submit(self, entries, *, engine_evidence, account_evidence):
        """Return ACCEPTED/BUSY/CLOSED/INVALID without waiting for model calls."""
        if not self._lock.acquire(blocking=False):
            return "BUSY"
        try:
            if self._closed:
                return "CLOSED"
            if self._busy:
                return "BUSY"
            # Bound even a generator before consuming the whole iterable.
            entries = tuple(islice(entries, 33))
            if not entries or len(entries) > 32 or any(not isinstance(e, PreparedEntry) for e in entries):
                return "INVALID"
            # Copy evidence before returning: producer mutations cannot change
            # the context reviewed for this immutable placement batch.
            evidence = json.dumps({"engine": engine_evidence, "account": account_evidence},
                                  allow_nan=False, separators=(",", ":"))
            if len(evidence.encode("utf-8")) > 65536:
                return "INVALID"
            snapshot = json.loads(evidence)
            if not isinstance(snapshot["engine"], dict) or not isinstance(snapshot["account"], dict):
                return "INVALID"
            self._busy = True
            self._thread = Thread(target=self._run, args=(entries, snapshot),
                                  name="atlas-grok-paper-entry", daemon=True)
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                self._busy = False
                return "UNAVAILABLE"
            return "ACCEPTED"
        except Exception:
            return "INVALID"
        finally:
            self._lock.release()

    def _run(self, entries, evidence):
        try:
            report = self.dispatcher.review_and_submit(entries,
                engine_evidence=evidence["engine"], account_evidence=evidence["account"],
                entry_enabled=lambda: not self._stop.is_set())
        except Exception:
            report = EntryDispatch("NOT_SENT", "ENTRY_WORKER_UNAVAILABLE")
        with self._lock:
            self._report = report

    def poll(self):
        """Consume one completion. Never waits for the provider."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            if self._report is None:
                return None
            report, self._report = self._report, None
            self._busy = False
            return report
        finally:
            self._lock.release()

    def close(self, timeout=0):
        """Request stop; return whether the in-flight review has exited.

        Provider transport is not forcibly interrupted. A False return means
        STOPPING, not a completed shutdown. The completion remains pollable.
        """
        if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 1:
            raise ValueError("Shutdown wait must be between zero and one second")
        self._stop.set()
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout)
        return thread is None or not thread.is_alive()

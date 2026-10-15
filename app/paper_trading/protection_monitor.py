"""Periodic supervision of the canonical PAPER feed gate; no transport or GUI."""
from math import isfinite
from threading import Event, Lock, Thread, current_thread

from app.paper_trading.protection_feed import PaperProtectionFeed


class PaperProtectionMonitor:
    """Poll during silence; shutdown terminally revokes this feed generation.

    Own one monitor per feed. Closing the monitor never closes the ledger host
    or releases its ownership lease. The lifecycle owner closes the transport,
    monitor and host in that order. Calls into the host remain cooperative;
    this thread cannot recover a dead process or interrupt blocked host I/O.
    """
    def __init__(self, feed, *, poll_seconds=0.25):
        if not isinstance(feed, PaperProtectionFeed):
            raise TypeError("Canonical protection feed required")
        if (isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float))
                or not isfinite(poll_seconds)
                or not 0.01 <= poll_seconds <= min(1, feed.silence_seconds / 2)):
            raise ValueError("Poll interval must be 0.01..1 seconds and at most half the silence limit")
        self._feed, self._interval = feed, poll_seconds
        self._lock, self._stop = Lock(), Event()
        self._thread = None
        self._state, self._feed_state = "CREATED", "NOT_CHECKED"
        self._checks, self._closed = 0, False
        feed._claim_monitor()

    def start(self):
        with self._lock:
            if self._closed or self._stop.is_set():
                raise RuntimeError("Protection monitor cannot restart")
            if self._thread is not None:
                return
            self._state = "RUNNING"
            self._thread = Thread(target=self._run, name="atlas-paper-feed-monitor", daemon=True)
            try:
                self._thread.start()
            except Exception:
                self._state, self._closed = "FAILED", True
                self._thread = None
                self._stop.set()
                self._feed.close()
                raise

    def _run(self):
        try:
            while not self._stop.is_set():
                state = self._feed.check_health()
                with self._lock:
                    self._checks += 1
                    self._feed_state = state
                if state == "CLOSED":
                    break
                if self._stop.wait(self._interval):
                    break
        except BaseException:
            # Never retain provider payloads or raw exception messages.
            self._stop.set()
            try:
                self._feed.close()
            except BaseException:
                pass  # Feed.close revokes its token before calling the host.
            with self._lock:
                self._state = "FAILED"
                self._feed_state = "CHECK_FAILED"
                self._closed = True
        finally:
            self._stop.set()
            with self._lock:
                if self._state != "FAILED":
                    self._state = "STOPPED"

    def snapshot(self):
        """Last monitor observation only; never performs a caller-side check."""
        with self._lock:
            return dict(scope="LOCAL_FEED_MONITOR_NOT_PROVIDER_OR_PROCESS_HEALTH",
                        state=self._state, last_feed_state=self._feed_state,
                        checks=self._checks, closed=self._closed,
                        running=self._thread is not None and self._thread.is_alive(),
                        poll_seconds=self._interval, live_feed_validated=False)

    def close(self, *, timeout_seconds=2):
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 5):
            raise ValueError("Finite shutdown join timeout from zero to five seconds required")
        with self._lock:
            self._closed = True
            self._stop.set()
            thread = self._thread
        failed = False
        try:
            self._feed.close()
        except Exception:
            failed = True
            with self._lock:
                self._state = "FAILED"
        if thread is not None and thread is not current_thread():
            thread.join(timeout_seconds)
        with self._lock:
            running = thread is not None and thread.is_alive()
            if not running and self._state != "FAILED":
                self._state = "STOPPED"
            return not running and not failed

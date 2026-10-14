"""Transport lifecycle gate for a trusted canonical PAPER feed adapter.

No SDK, network, thread or provider status inference lives here. The transport
owner must call check_health regularly and disconnect on any transport failure.
"""
from math import isfinite
from threading import RLock
from time import monotonic

from app.market_data.models import MarketEventType
from app.market_data.validation import validate_event


class PaperProtectionFeed:
    """Reject old connection callbacks and require new status after outages.

    Tokens are process-local identity objects, not serializable credentials.
    Only a verified adapter may supply events; a source label does not prove
    provider provenance. All callbacks must use this gate, not host.process.
    """
    def __init__(self, host, *, source, silence_seconds, clock=monotonic):
        if not isinstance(source, str) or not source.strip() or source != source.strip():
            raise ValueError("Explicit canonical feed source required")
        if (isinstance(silence_seconds, bool) or not isinstance(silence_seconds, (int, float))
                or not isfinite(silence_seconds) or not 0 < silence_seconds <= 5):
            raise ValueError("Feed silence limit must be positive and at most five seconds")
        self._host, self._source, self._limit, self._clock = host, source, silence_seconds, clock
        self._lock = RLock()
        self._token = None
        self._deadline = None
        self._last_clock = None
        self._state = "DISCONNECTED"
        host.pause_matching()

    def _pause(self, state):
        self._token, self._deadline, self._state = None, None, state
        self._host.pause_matching()

    def _now(self):
        at = self._clock()
        if not isinstance(at, (int, float)) or isinstance(at, bool) or not isfinite(at):
            self._pause("CLOCK_INVALID")
            raise ValueError("Finite monotonic clock required")
        if self._last_clock is not None and at < self._last_clock:
            self._pause("CLOCK_INVALID")
            raise ValueError("Monotonic clock moved backwards")
        self._last_clock = at
        return at

    def connect(self):
        """Start a fresh generation; connection success never implies RESUME."""
        with self._lock:
            self._pause("DISCONNECTED")
            at = self._now()
            self._token = object()
            self._deadline = at + self._limit
            self._state = "CONNECTED_AWAITING_STATUS"
            return self._token

    def disconnect(self, token):
        with self._lock:
            if token is not self._token or self._token is None:
                return "OLD_CONNECTION"
            self._pause("DISCONNECTED")
            return "DISCONNECTED"

    def check_health(self):
        """Called by the future transport loop even when no messages arrive."""
        with self._lock:
            at = self._now()
            if self._token is not None and at >= self._deadline:
                self._pause("FEED_SILENT")
            return self._state

    def process(self, token, event):
        with self._lock:
            # Rejected generations cannot close or keep alive the current one.
            if token is not self._token or self._token is None:
                return "OLD_CONNECTION"
            self.check_health()
            if self._token is None:
                return self._state
            try:
                validate_event(event)
                if event.source != self._source:
                    self._pause("SOURCE_MISMATCH")
                    return "SOURCE_MISMATCH"
                if event.event_type not in {
                    MarketEventType.QUOTE, MarketEventType.RESUME, MarketEventType.TRADING_HALT,
                }:
                    # Heartbeats/trades do not prove quote or status health.
                    return "UNSUPPORTED_PROTECTION_EVENT"
                result = self._host.process(event)
            except Exception:
                self._pause("EVENT_FAILURE")
                raise
            if result == "STALE_OR_FUTURE_EVENT":
                self._pause("INVALID_EVENT_TIME")
            elif result in {"PROCESSED", "RESUMED", "HALTED"}:
                if result == "RESUMED":
                    self._state = "CONNECTED"
                # A halted quote cannot keep a connection alive indefinitely.
                # Only accepted status or matched/considered quotes renew it.
                if result != "HALTED" or event.event_type is MarketEventType.TRADING_HALT:
                    self._deadline = self._now() + self._limit
            return result

    def snapshot(self):
        with self._lock:
            self.check_health()
            return dict(scope="CANONICAL_FEED_LIFECYCLE_NOT_PROVIDER_VALIDATION",
                        state=self._state, connected=self._token is not None,
                        entries_enabled=False, live_feed_validated=False)

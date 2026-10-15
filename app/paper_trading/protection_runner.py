"""Single-owner transport loop for trusted, normalized PAPER protection feeds.

This module supplies lifecycle wiring, not provider verification. In particular,
the Webull quote-field probe is NOT a verified share-size or status adapter.
"""
from collections import Counter
from math import isfinite
from threading import Lock
from time import monotonic

from app.market_data.models import MarketEventType
from app.paper_trading.protection_feed import PaperProtectionFeed
from app.paper_trading.protection_monitor import PaperProtectionMonitor


class PaperProtectionRunner:
    """Run bounded receives and reconnects; retain caller ownership of the host.

    backend_factory() returns a fresh, exclusively owned backend with connect,
    subscribe(tuple), receive(), disconnect(), registration_ready and integer
    generation_metrics['generation']. Receive MUST have a bounded timeout.

    quote_decoder_factory(symbols, generation) returns a callable producing only
    canonical QUOTE events with sizes already verified and normalized to shares.
    open_status(symbols, emit) returns a closeable verified halt/resume adapter
    with a boolean healthy property, false on status-channel interruption.
    It must subscribe afresh each attempt and preserve provider timestamps. All
    events use the explicit common source. These are trusted adapter contracts,
    not permissions or provenance inferred from a source string.

    No adapters are supplied by default; missing adapters block before network.
    run is single-use, including failed/blocked runs. Stop the transport and
    monitor successfully before the caller closes the host and its ledger lease.
    """
    def __init__(self, host, backend_factory, *, source,
                 quote_decoder_factory=None, open_status=None,
                 max_attempts=3, retry_seconds=1, silence_seconds=5,
                 clock=monotonic):
        if not callable(backend_factory):
            raise TypeError("Backend factory required")
        if any(adapter is not None and not callable(adapter)
               for adapter in (quote_decoder_factory, open_status)):
            raise TypeError("Callable trusted adapters required")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
            raise ValueError("One to five connection attempts required")
        if (type(retry_seconds) not in (int, float) or not isfinite(retry_seconds)
                or not 0.01 <= retry_seconds <= 30):
            raise ValueError("Retry delay must be 0.01..30 seconds")
        self._host, self._backend_factory = host, backend_factory
        self._decode_factory, self._open_status = quote_decoder_factory, open_status
        self._max_attempts, self._retry = max_attempts, retry_seconds
        self._feed = PaperProtectionFeed(host, source=source,
                                         silence_seconds=silence_seconds, clock=clock)
        self._claim, self._used = Lock(), False

    def run(self, stop):
        """Block until flat, stopped or attempts exhausted; never submit entries.

        stop is a threading.Event-compatible object. Factory/open/close calls
        must also be bounded and cooperative. This is not a process supervisor.
        Failed shutdown is terminal and forbids reconnecting over old resources.
        """
        with self._claim:
            if self._used:
                raise RuntimeError("Protection runner cannot restart")
            self._used = True
        attempts, errors, state = 0, Counter(), "STOPPED"
        monitor = None
        cleanup_ok = True
        try:
            if not self._host.protection_symbols:
                state = "FLAT"
            elif self._decode_factory is None or self._open_status is None:
                state = "BLOCKED_UNVERIFIED_ADAPTERS"
            elif not stop.is_set():
                monitor = PaperProtectionMonitor(self._feed)
                monitor.start()
                while not stop.is_set() and attempts < self._max_attempts:
                    if monitor.snapshot()["state"] != "RUNNING":
                        state = "MONITOR_FAILED"
                        break
                    symbols = self._host.protection_symbols
                    if not symbols:
                        state = "FLAT"
                        break
                    attempts += 1
                    backend = status = token = None
                    try:
                        backend = self._backend_factory()
                        backend.connect()
                        if stop.is_set():
                            state = "STOPPED"
                            break
                        if not backend.registration_ready:
                            raise RuntimeError("Registration unavailable")
                        generation = backend.generation_metrics["generation"]
                        if type(generation) is not int or generation <= 0:
                            raise ValueError("Invalid transport generation")
                        decode = self._decode_factory(symbols, generation)
                        if not callable(decode):
                            raise TypeError("Quote decoder required")
                        backend.subscribe(symbols)
                        token = self._feed.connect()

                        def emit(event, connection=token):
                            # Invalid status callbacks also revoke their own
                            # generation, even if an SDK swallows the exception.
                            if getattr(event, "event_type", None) not in {
                                MarketEventType.RESUME, MarketEventType.TRADING_HALT,
                            }:
                                self._feed.disconnect(connection)
                                raise ValueError("Only verified status events permitted")
                            return self._feed.process(connection, event)

                        status = self._open_status(symbols, emit)
                        if not callable(getattr(status, "close", None)):
                            raise TypeError("Closeable status adapter required")
                        state = "CONNECTED_AWAITING_STATUS"
                        while not stop.is_set():
                            if not self._host.protection_symbols:
                                state = "FLAT"
                                break
                            if (status.healthy is not True or not backend.registration_ready
                                    or backend.generation_metrics["generation"] != generation
                                    or not self._feed.snapshot()["connected"]):
                                raise RuntimeError("Connection interrupted")
                            envelope = backend.receive()
                            if stop.is_set():
                                break
                            # Do not consume a queued quote after registration
                            # loss or an unnoticed backend generation change.
                            if (status.healthy is not True or not backend.registration_ready
                                    or backend.generation_metrics["generation"] != generation):
                                raise RuntimeError("Connection interrupted")
                            if envelope is None:
                                continue
                            event = decode(envelope)
                            if event is not None:
                                if event.event_type is not MarketEventType.QUOTE:
                                    raise ValueError("Quote adapter cannot authorize status")
                                self._feed.process(token, event)
                        if stop.is_set():
                            state = "STOPPED"
                    except Exception:
                        errors["CONNECTION_OR_ADAPTER_FAILURE"] += 1
                        state = "ATTEMPTS_EXHAUSTED"
                    finally:
                        # Retire authority BEFORE SDK cleanup can emit callbacks.
                        try:
                            if token is not None:
                                self._feed.disconnect(token)
                        except Exception:
                            cleanup_ok = False
                        for resource, method in ((status, "close"), (backend, "disconnect")):
                            if resource is not None:
                                try:
                                    getattr(resource, method)()
                                except Exception:
                                    cleanup_ok = False
                    if stop.is_set():
                        state = "STOPPED"
                    if not cleanup_ok or state in {"FLAT", "STOPPED"}:
                        break
                    if monitor.snapshot()["state"] != "RUNNING":
                        state = "MONITOR_FAILED"
                        break
                    if attempts < self._max_attempts and stop.wait(self._retry):
                        state = "STOPPED"
                        break
        finally:
            if monitor is not None:
                cleanup_ok = monitor.close() and cleanup_ok
            else:
                self._feed.close()
        if not cleanup_ok:
            state = "SHUTDOWN_FAILED"
        return dict(scope="TRUSTED_ADAPTER_PAPER_TRANSPORT_NOT_PROVIDER_VALIDATION",
                    state=state, attempts=attempts, errors=dict(errors),
                    shutdown_complete=cleanup_ok, entries_enabled=False,
                    live_feed_validated=False)

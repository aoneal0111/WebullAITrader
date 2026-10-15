from dataclasses import replace
from datetime import timedelta
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

from app.gui.runtime_ownership import acquire_runtime_ownership
from app.market_data.models import MarketEvent, MarketEventType, ResumePayload
from app.paper_trading.protection_host import PaperProtectionHost
from app.paper_trading.protection_host_check import T0, seed, quote
from app.paper_trading.protection_runner import PaperProtectionRunner

SOURCE = "synthetic-protection-check"


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "paper-execution.sqlite3"
    seed(path)
    wall, mono = [T0 + timedelta(seconds=2)], [100.0]
    host = PaperProtectionHost(path, environment="PAPER", clock=lambda: wall[0])
    def advance():
        wall[0] += timedelta(milliseconds=100)
        mono[0] += .1
        return wall[0]
    def resume():
        return MarketEvent(2, advance(), "DEMO", SOURCE, MarketEventType.RESUME, ResumePayload("synthetic"))
    yield host, path, advance, resume, wall, mono
    host.close()


class Backend:
    def __init__(self, receive, *, disconnect=None):
        self.registration_ready = False
        self.generation_metrics = {"generation": 1}
        self.subscriptions, self.closed = [], False
        self._receive, self._disconnect = receive, disconnect

    def connect(self):
        self.registration_ready = True

    def subscribe(self, symbols):
        self.subscriptions.append(symbols)

    def receive(self):
        return self._receive(self)

    def disconnect(self):
        self.closed = True
        self.registration_ready = False
        if self._disconnect:
            self._disconnect()


def runner(host, factory, *, status, **kwargs):
    return PaperProtectionRunner(host, factory, source=SOURCE,
        quote_decoder_factory=lambda symbols, generation: lambda envelope: envelope,
        open_status=status, retry_seconds=.01, **kwargs)


def test_exact_inventory_subscription_partial_fill_flat_shutdown_and_lease_retained(setup):
    host, path, advance, resume, _, mono = setup
    assert host.protection_symbols == ("DEMO",)
    backend = Backend(lambda _: quote(advance(), size="4"))
    callbacks, closes = [], []
    def status(symbols, emit):
        assert symbols == ("DEMO",)
        callbacks.append(emit)
        assert emit(resume()) == "RESUMED"
        return SimpleNamespace(healthy=True, close=lambda: closes.append(True))
    result = runner(host, lambda: backend, status=status, clock=lambda: mono[0]).run(Event())
    assert result["state"] == "FLAT"
    assert result["attempts"] == 1 and result["shutdown_complete"]
    assert result["entries_enabled"] is result["live_feed_validated"] is False
    assert host.protection_symbols == () and host._quantity("DEMO") == 0
    assert backend.subscriptions == [("DEMO",)] and backend.closed and closes == [True]
    assert callbacks[0](resume()) == "OLD_CONNECTION"
    assert acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock")) is None
    assert host.snapshot()["state"] == "PROTECTION_ONLY"


@pytest.mark.parametrize("missing", ["quote", "status", "both"])
def test_unverified_adapters_block_without_network_and_keep_inventory_paused(setup, missing):
    host, _, _, resume, _, _ = setup
    def unexpected(*args):
        pytest.fail("No adapter or network may start")
    obj = PaperProtectionRunner(host, unexpected, source=SOURCE,
        quote_decoder_factory=None if missing in {"quote", "both"} else unexpected,
        open_status=None if missing in {"status", "both"} else unexpected)
    result = obj.run(Event())
    assert result["state"] == "BLOCKED_UNVERIFIED_ADAPTERS" and result["attempts"] == 0
    assert host.snapshot()["matching_paused_symbols"] == 1
    with pytest.raises(RuntimeError, match="restart"):
        obj.run(Event())


def test_flat_host_never_constructs_transport(setup):
    host, _, advance, resume, _, _ = setup
    host.process(resume())
    host.process(quote(advance()))
    obj = PaperProtectionRunner(host, lambda: pytest.fail("flat network"), source=SOURCE)
    assert obj.run(Event())["state"] == "FLAT"


def test_stopped_before_run_never_constructs_transport(setup):
    host, _, _, _, _, _ = setup
    stop = Event()
    stop.set()
    result = runner(host, lambda: pytest.fail("stopped network"), status=lambda *_: None).run(stop)
    assert result["state"] == "STOPPED" and result["attempts"] == 0


def test_reconnect_resubscribes_retires_callbacks_and_requires_new_status(setup):
    host, _, advance, resume, _, mono = setup
    callbacks, backends, pending = [], [], [True]
    def receive(backend):
        if len(backends) == 1:
            backend.registration_ready = False
            return quote(advance())  # Must not be matched after loss of registration.
        if pending.pop() if pending else False:
            assert callbacks[0](resume()) == "OLD_CONNECTION"
            assert host._quantity("DEMO") == 10
            assert host.snapshot()["matching_paused_symbols"] == 1
            # Fresh quote before this generation's status must not fill.
            return quote(advance())
        assert host._quantity("DEMO") == 10
        callbacks[-1](resume())
        return quote(advance())
    def factory():
        backend = Backend(receive)
        backends.append(backend)
        return backend
    def status(symbols, emit):
        callbacks.append(emit)
        if len(callbacks) == 1:
            emit(resume())
        def close():
            assert emit(resume()) == "OLD_CONNECTION"
        return SimpleNamespace(healthy=True, close=close)
    result = runner(host, factory, status=status, clock=lambda: mono[0]).run(Event())
    assert result["state"] == "FLAT" and result["attempts"] == 2
    assert all(b.closed and b.subscriptions == [("DEMO",)] for b in backends)
    assert host._quantity("DEMO") == 0


@pytest.mark.parametrize("fault", ["decode", "quote_as_status", "status_as_quote", "malformed_status",
                                    "generation", "connect", "subscribe", "status_factory"])
def test_faults_pause_and_exhaust_bounded_retries_without_fill(setup, fault):
    host, _, advance, resume, _, mono = setup
    backends = []
    def factory():
        def receive(backend):
            if fault == "generation":
                backend.generation_metrics["generation"] += 1
            if fault == "decode":
                return replace(quote(advance()), source="wrong-source")
            if fault == "status_as_quote":
                return resume()
            raise RuntimeError("No valid quote")
        backend = Backend(receive)
        if fault == "connect":
            backend.connect = lambda: (_ for _ in ()).throw(RuntimeError("connect"))
        if fault == "subscribe":
            backend.subscribe = lambda _: (_ for _ in ()).throw(RuntimeError("subscribe"))
        backends.append(backend)
        return backend
    def status(symbols, emit):
        if fault in {"quote_as_status", "malformed_status"}:
            # Simulate SDK swallowing a callback error: generation must still revoke.
            with pytest.raises(ValueError):
                emit(quote(advance()) if fault == "quote_as_status" else object())
        elif fault == "status_factory":
            raise RuntimeError("Status factory cleans its own failed open")
        else:
            emit(resume())
        return SimpleNamespace(healthy=True, close=lambda: None)
    result = runner(host, factory, status=status, clock=lambda: mono[0], max_attempts=2).run(Event())
    assert result["state"] == "ATTEMPTS_EXHAUSTED" and result["attempts"] == 2
    assert result["shutdown_complete"] and all(b.closed for b in backends)
    assert host._quantity("DEMO") == 10 and host.snapshot()["matching_paused_symbols"] == 1


@pytest.mark.parametrize("failure", ["status", "backend"])
def test_cleanup_failure_forbids_reconnect_and_retains_host_lease(setup, failure):
    host, path, _, _, _, _ = setup
    backends = []
    def fail():
        raise RuntimeError("cleanup")
    def factory():
        backend = Backend(lambda _: fail(), disconnect=fail if failure == "backend" else None)
        backends.append(backend)
        return backend
    result = runner(host, factory, status=lambda *_: SimpleNamespace(healthy=True,
        close=fail if failure == "status" else lambda: None)).run(Event())
    assert result["state"] == "SHUTDOWN_FAILED" and not result["shutdown_complete"]
    assert len(backends) == 1 and backends[0].closed
    assert acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock")) is None
    assert host._quantity("DEMO") == 10


def test_stop_during_receive_discards_envelope_and_closes_all_resources(setup):
    host, _, advance, resume, _, _ = setup
    stop, closed = Event(), []
    def receive(_):
        stop.set()
        return quote(advance())
    backend = Backend(receive)
    def status(_, emit):
        emit(resume())
        return SimpleNamespace(healthy=True, close=lambda: closed.append(True))
    result = runner(host, lambda: backend, status=status).run(stop)
    assert result["state"] == "STOPPED" and result["shutdown_complete"]
    assert backend.closed and closed == [True] and host._quantity("DEMO") == 10


def test_status_channel_loss_discards_queued_quote(setup):
    host, _, advance, resume, _, _ = setup
    handle = SimpleNamespace(healthy=True, close=lambda: None)
    def status(_, emit):
        emit(resume())
        return handle
    def receive(_):
        handle.healthy = False
        return quote(advance())
    backend = Backend(receive)
    result = runner(host, lambda: backend, status=status, max_attempts=1).run(Event())
    assert result["state"] == "ATTEMPTS_EXHAUSTED"
    assert backend.closed and host._quantity("DEMO") == 10
    assert host.snapshot()["matching_paused_symbols"] == 1


def test_monitor_pauses_during_blocked_receive_without_runner_health_call(setup):
    host, _, _, resume, wall, mono = setup
    stop = Event()
    def receive(_):
        wall[0] += timedelta(seconds=5)
        mono[0] += 5
        deadline = monotonic() + 3
        # Read host state only: the runner remains inside this receive call.
        while host.snapshot()["matching_paused_symbols"] == 0 and monotonic() < deadline:
            stop.wait(.01)
        assert host.snapshot()["matching_paused_symbols"] == 1
        stop.set()
        return None
    backend = Backend(receive)
    def status(_, emit):
        emit(resume())
        assert host.snapshot()["matching_paused_symbols"] == 0
        return SimpleNamespace(healthy=True, close=lambda: None)
    result = runner(host, lambda: backend, status=status, clock=lambda: mono[0]).run(stop)
    assert result["state"] == "STOPPED" and result["shutdown_complete"]
    assert host._quantity("DEMO") == 10 and backend.closed


def test_monitor_failed_shutdown_report_never_releases_host(setup, monkeypatch):
    host, path, _, _, _, _ = setup
    from app.paper_trading.protection_monitor import PaperProtectionMonitor
    original = PaperProtectionMonitor.close
    def failed_join(self):
        assert original(self)
        return False
    monkeypatch.setattr(PaperProtectionMonitor, "close", failed_join)
    stop = Event()
    backend = Backend(lambda _: stop.set())
    result = runner(host, lambda: backend,
                    status=lambda *_: SimpleNamespace(healthy=True, close=lambda: None)).run(stop)
    assert result["state"] == "SHUTDOWN_FAILED" and not result["shutdown_complete"]
    assert acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock")) is None


def test_interruptible_retry_and_keyboard_interrupt_cleanup(setup):
    host, _, _, _, _, _ = setup
    backend = Backend(lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    obj = runner(host, lambda: backend, status=lambda *_: SimpleNamespace(healthy=True, close=lambda: None))
    with pytest.raises(KeyboardInterrupt):
        obj.run(Event())
    assert backend.closed and host.snapshot()["matching_paused_symbols"] == 1
    class StopOnWait:
        def is_set(self):
            return False
        def wait(self, seconds):
            assert seconds == .01
            return True
    def fail():
        raise RuntimeError("offline")
    result = runner(host, fail, status=lambda *_: None).run(StopOnWait())
    assert result["state"] == "STOPPED" and result["attempts"] == 1


@pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"max_attempts": True},
    {"max_attempts": 6}, {"retry_seconds": float("nan")}, {"retry_seconds": True},
    {"retry_seconds": 0}, {"retry_seconds": 31}, {"quote_decoder_factory": True},
    {"open_status": True}])
def test_invalid_configuration_rejected(setup, kwargs):
    with pytest.raises((ValueError, TypeError)):
        PaperProtectionRunner(setup[0], lambda: None, source=SOURCE, **kwargs)

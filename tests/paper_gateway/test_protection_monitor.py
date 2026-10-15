import json
from datetime import timedelta
from threading import Event, current_thread
from time import monotonic
from types import SimpleNamespace

import pytest

from app.paper_trading.protection_feed import PaperProtectionFeed
from app.paper_trading.protection_monitor import PaperProtectionMonitor


def wait_for(predicate):
    deadline = monotonic() + 2
    pause = Event()
    while not predicate():
        assert monotonic() < deadline, "Monitor did not reach expected state"
        pause.wait(.005)


@pytest.fixture
def setup():
    pauses = []
    clock = [100.0]
    host = SimpleNamespace(pause_matching=lambda: pauses.append(True))
    feed = PaperProtectionFeed(host, source="test", silence_seconds=1, clock=lambda: clock[0])
    monitor = PaperProtectionMonitor(feed, poll_seconds=.01)
    yield feed, monitor, clock, pauses
    monitor.close()


def test_silence_is_detected_without_callbacks_or_caller_health_checks(setup, monkeypatch):
    feed, monitor, clock, pauses = setup
    token = feed.connect()
    original = feed.check_health
    callers = []
    def check():
        callers.append(current_thread().name)
        return original()
    monkeypatch.setattr(feed, "check_health", check)
    monitor.start()
    wait_for(lambda: monitor.snapshot()["checks"] > 0)
    before = len(pauses)
    clock[0] += 1
    wait_for(lambda: monitor.snapshot()["last_feed_state"] == "FEED_SILENT")
    assert len(pauses) > before
    assert set(callers) == {"atlas-paper-feed-monitor"}
    assert feed.disconnect(token) == "OLD_CONNECTION"
    # Reconnection is allowed after silence but still requires fresh status.
    assert feed.connect() is not token


def test_start_is_explicit_idempotent_and_shutdown_terminal(setup):
    feed, monitor, _, _ = setup
    token = feed.connect()
    assert monitor.snapshot()["state"] == "CREATED"
    assert not monitor.snapshot()["running"]
    monitor.start()
    thread = monitor._thread
    monitor.start()
    assert monitor._thread is thread
    assert monitor.close()
    assert monitor.close()
    assert monitor.snapshot()["state"] == "STOPPED"
    assert feed.check_health() == "CLOSED"
    assert feed.process(token, object()) == "OLD_CONNECTION"
    with pytest.raises(RuntimeError, match="closed"):
        feed.connect()
    with pytest.raises(RuntimeError, match="restart"):
        monitor.start()


@pytest.mark.parametrize("exception", [RuntimeError, SystemExit])
def test_monitor_failure_revokes_callbacks_and_sanitizes_snapshot(setup, monkeypatch, exception):
    feed, monitor, _, _ = setup
    token = feed.connect()
    def fail():
        raise exception("PRIVATE_PROVIDER_PAYLOAD")
    monkeypatch.setattr(feed, "check_health", fail)
    monitor.start()
    wait_for(lambda: monitor.snapshot()["state"] == "FAILED")
    assert feed.process(token, object()) == "OLD_CONNECTION"
    assert "PRIVATE_PROVIDER_PAYLOAD" not in json.dumps(monitor.snapshot())
    assert monitor.close()
    assert monitor.snapshot()["state"] == "FAILED"


def test_monitor_clock_fault_is_terminal(setup):
    feed, monitor, clock, _ = setup
    token = feed.connect()
    monitor.start()
    wait_for(lambda: monitor.snapshot()["checks"] > 0)
    clock[0] -= 1
    wait_for(lambda: monitor.snapshot()["state"] == "FAILED")
    assert feed.process(token, object()) == "OLD_CONNECTION"
    with pytest.raises(RuntimeError, match="closed"):
        feed.connect()


def test_shutdown_reports_blocked_check_until_it_finishes(setup, monkeypatch):
    feed, monitor, _, _ = setup
    entered, release = Event(), Event()
    def blocked():
        entered.set()
        assert release.wait(2)
        return "DISCONNECTED"
    monkeypatch.setattr(feed, "check_health", blocked)
    try:
        monitor.start()
        assert entered.wait(1)
        assert not monitor.close(timeout_seconds=0)
        assert monitor.snapshot()["running"]
    finally:
        release.set()
    assert monitor.close()
    assert not monitor.snapshot()["running"]


def test_close_without_start_and_duplicate_monitor_are_safe(setup):
    feed, monitor, _, _ = setup
    with pytest.raises(RuntimeError, match="claimed"):
        PaperProtectionMonitor(feed, poll_seconds=.01)
    assert monitor.close()
    assert monitor.snapshot()["checks"] == 0
    with pytest.raises(RuntimeError, match="closed"):
        PaperProtectionMonitor(feed, poll_seconds=.01)


def test_thread_start_failure_revokes_feed(setup, monkeypatch):
    feed, monitor, _, _ = setup
    token = feed.connect()
    def fail_start(_thread):
        raise RuntimeError("thread unavailable")
    monkeypatch.setattr("app.paper_trading.protection_monitor.Thread.start", fail_start)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        monitor.start()
    assert monitor.snapshot()["state"] == "FAILED"
    assert feed.process(token, object()) == "OLD_CONNECTION"
    assert monitor.close()


@pytest.mark.parametrize("poll", [0, .001, 1.1, .6, True, float("nan"), float("inf")])
def test_invalid_poll_interval_rejected(poll):
    feed = PaperProtectionFeed(SimpleNamespace(pause_matching=lambda: None),
                               source="test", silence_seconds=1)
    with pytest.raises(ValueError):
        PaperProtectionMonitor(feed, poll_seconds=poll)


@pytest.mark.parametrize("timeout", [-1, 6, True, float("nan"), float("inf")])
def test_invalid_shutdown_timeout_does_not_stop_monitor(setup, timeout):
    feed, monitor, _, _ = setup
    token = feed.connect()
    with pytest.raises(ValueError):
        monitor.close(timeout_seconds=timeout)
    assert not monitor.snapshot()["closed"]
    assert feed.disconnect(token) == "DISCONNECTED"


def test_shutdown_preserves_protective_orders_inventory_and_host_lease(tmp_path):
    from app.gui.runtime_ownership import acquire_runtime_ownership
    from app.paper_trading.protection_host import PaperProtectionHost
    from app.paper_trading.protection_host_check import T0, seed
    path = tmp_path / "paper.sqlite3"
    seed(path)
    host = PaperProtectionHost(path, environment="PAPER", clock=lambda: T0 + timedelta(seconds=2))
    feed = PaperProtectionFeed(host, source="test", silence_seconds=1)
    monitor = PaperProtectionMonitor(feed, poll_seconds=.01)
    try:
        orders = [(o.order_id, o.remaining_quantity) for o in host._commands.order_book.open_orders()]
        monitor.start()
        assert monitor.close()
        assert host._quantity("DEMO") == 10
        assert orders == [(o.order_id, o.remaining_quantity) for o in host._commands.order_book.open_orders()]
        assert host.snapshot()["matching_paused_symbols"] == 1
        assert acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock")) is None
    finally:
        monitor.close()
        host.close()

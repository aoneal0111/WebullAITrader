from collections import deque
from threading import Event
from types import SimpleNamespace

from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver


def _driver(scanner, *, watchdog=None):
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._scanner = scanner
    driver._market_data_stop = Event()
    driver._market_data_consumer_state = "RUNNING"
    driver._market_data_consumer_attempted = False
    driver._market_data_first_event_consumed = False
    driver._market_data_failure_stage = None
    driver._market_data_failure_exception_class = None
    driver._scanner_events_since_observation = 0
    driver._cycles_completed = 0
    driver._reconcile_temporal_orders = lambda: None
    driver._publish_scanner_observation_if_due = lambda **_: None
    driver._scanner_publisher = SimpleNamespace(publish=lambda *args, **kwargs: ())
    driver._timestamp = lambda: None
    driver._run_feed_watchdog = watchdog or (lambda: None)
    driver._publish_terminal_market_data_failure = lambda exc: (_ for _ in ()).throw(exc)
    return driver


def test_callbacks_buffered_before_consumer_start_are_drained_without_false_stale():
    watchdog_calls = []

    class Scanner:
        connected = True
        running = True

        def __init__(self):
            self.cycles = deque((SimpleNamespace(events_read=1), SimpleNamespace(events_read=0)))
            self.run_calls = 0

        def run_available(self):
            self.run_calls += 1
            if self.run_calls == 2:
                driver._market_data_stop.set()
            return self.cycles.popleft()

        def snapshot(self):
            return object()

    scanner = Scanner()
    driver = _driver(scanner, watchdog=lambda: watchdog_calls.append(scanner.run_calls))
    driver._receive_market_data(Event())

    assert scanner.run_calls == 2
    assert driver._market_data_first_event_consumed is True
    # The first buffered callback was given a receive opportunity before any
    # startup stale/reconnect decision was made.
    assert watchdog_calls[0] == 1


def test_consumer_waits_for_scanner_runnable_state_during_reconnect():
    class Scanner:
        connected = False
        running = False
        run_calls = 0

        def run_available(self):
            self.run_calls += 1
            return SimpleNamespace(events_read=1)

    scanner = Scanner()
    driver = _driver(scanner)

    def stop_after_wait():
        driver._market_data_stop.set()

    driver._market_data_stop.wait = lambda _seconds: (stop_after_wait(), True)[1]
    driver._receive_market_data(Event())

    assert scanner.run_calls == 0
    assert driver._market_data_consumer_state == "RUNNING"

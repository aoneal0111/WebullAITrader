from __future__ import annotations

from types import SimpleNamespace

import app.services.runtime_drivers.broker as broker_module
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver


class _Transport:
    last_normalized_event_monotonic = None


class _Scanner:
    def __init__(self) -> None:
        self._transport = _Transport()
        self.recoveries = 0

    def recover_stream(self):
        self.recoveries += 1
        return ("AAA",)


def _driver(scanner: _Scanner):
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._configuration = SimpleNamespace(
        maximum_market_data_age_seconds=5,
        market_data_reconnect_after_seconds=10,
        suspend_gap_detection_seconds=10,
        stream_reconnect_backoff_seconds=1,
    )
    driver._scanner = scanner
    driver._market_data = None
    driver._market_data_started_monotonic = 0.0
    driver._last_runtime_iteration_monotonic = 0.0
    driver._last_driver_market_event_monotonic = None
    driver._feed_stale = False
    driver._feed_recovery_pending = False
    driver._feed_recovery_started_monotonic = None
    driver._last_feed_recovery_at = 0.0
    driver._health = []
    driver._publish_health = lambda event_type, message, health, **kwargs: (
        driver._health.append((event_type, health))
    )
    driver._timestamp = lambda: __import__("datetime").datetime.now(
        __import__("datetime").UTC
    )
    return driver


def test_payload_silence_marks_stale_then_recovers_only_after_new_payload(monkeypatch):
    scanner = _Scanner()
    driver = _driver(scanner)
    clock = iter((6.0, 11.0, 12.0))
    monkeypatch.setattr(broker_module, "monotonic", lambda: next(clock))

    driver._run_feed_watchdog()
    assert driver._feed_stale is True
    assert driver._health[-1][0] == "MARKET_DATA_STALE"
    assert driver._health[-1][1].execution_status == "BLOCKED - STALE DATA"

    driver._run_feed_watchdog()
    assert scanner.recoveries == 1
    assert driver._feed_recovery_pending is True

    # Reconnect/transport success alone is insufficient: no payload marker yet.
    driver._run_feed_watchdog()
    assert driver._feed_recovery_pending is True

    scanner._transport.last_normalized_event_monotonic = 12.0
    driver._complete_feed_recovery()
    assert driver._feed_stale is False
    assert driver._feed_recovery_pending is False
    assert driver._health[-1][0] == "MARKET_DATA_RECONNECTED"
    assert driver._health[-1][1].execution_status == "ENABLED"


def test_temporal_reconciliation_is_independent_of_market_events():
    scanner = _Scanner()
    driver = _driver(scanner)
    calls = []
    driver._temporal_reconciler = lambda **kwargs: calls.append(kwargs)

    driver._reconcile_temporal_orders()

    assert len(calls) == 1
    assert "at" in calls[0]

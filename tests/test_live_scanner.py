from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from time import monotonic, sleep

import pytest

from app.live_scanner import (
    LiveScannerCoordinator,
    ReceiveTransportAdapter,
)
from app.market_data.stream import iter_available
from app.momentum_scanner import AssetClass
from app.performance_diagnostics import performance_diagnostics


@dataclass(frozen=True)
class FakeEvent:
    symbol: str


class FakeTransport:
    def __init__(
        self,
        events: list[Any] | None = None,
    ) -> None:
        self.events = list(events or [])
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.subscriptions: list[
            tuple[str, ...]
        ] = []

    def connect(self) -> None:
        self.connected = True
        self.connect_calls += 1

    def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def subscribe(
        self,
        channels: tuple[str, ...],
    ) -> None:
        self.subscriptions.append(channels)

    def read_event(self) -> Any | None:
        if not self.events:
            return None

        return self.events.pop(0)


class FakeReceiveClient:
    def __init__(self) -> None:
        self.events: list[Any] = []
        self.connected = False
        self.channels: tuple[str, ...] = ()

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(
        self,
        channels: tuple[str, ...],
    ) -> None:
        self.channels = channels

    def receive(self) -> Any | None:
        if not self.events:
            return None

        return self.events.pop(0)


class FakeEngine:
    def __init__(self) -> None:
        self.active_symbols = ("AAA", "BTCUSD")
        self.events: list[Any] = []
        self.refresh_calls: list[
            tuple[tuple[AssetClass, ...], bool]
        ] = []
        self.snapshot_limits: list[int] = []
        self.decisions: dict[str, object | None] = {}

    def refresh_universe(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
        *,
        force_reference_refresh: bool = False,
    ) -> tuple[str, ...]:
        self.refresh_calls.append(
            (
                asset_classes,
                force_reference_refresh,
            )
        )
        return self.active_symbols

    def consume(self, event: Any) -> object | None:
        self.events.append(event)
        return self.decisions.get(event.symbol)

    def snapshot(
        self,
        *,
        limit: int = 25,
    ) -> dict[str, int]:
        self.snapshot_limits.append(limit)
        return {"limit": limit}


def test_receive_adapter_maps_receive_to_read_event() -> None:
    client = FakeReceiveClient()
    event = FakeEvent("AAA")
    client.events.append(event)

    adapter = ReceiveTransportAdapter(client)

    adapter.connect()
    adapter.subscribe(("quotes",))

    assert adapter.read_event() is event
    assert client.connected is True
    assert client.channels == ("quotes",)

    adapter.disconnect()

    assert client.connected is False


def test_start_connects_subscribes_and_refreshes() -> None:
    transport = FakeTransport()
    engine = FakeEngine()

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=(
            "trades",
            "quotes",
            "trades",
        ),
    )

    active = coordinator.start(
        force_reference_refresh=True
    )

    assert active == ("AAA", "BTCUSD")
    assert transport.connect_calls == 1
    assert transport.subscriptions == [
        ("quotes", "trades")
    ]
    assert engine.refresh_calls == [
        (
            (
                AssetClass.STOCK,
                AssetClass.CRYPTO,
            ),
            True,
        )
    ]
    assert coordinator.running is True


def test_running_scanner_refreshes_discovery_off_event_loop() -> None:
    transport = FakeTransport()
    engine = FakeEngine()
    coordinator = LiveScannerCoordinator(
        transport, engine, universe_refresh_interval_seconds=0.01,
    )
    coordinator.start()
    deadline = monotonic() + 1.0
    while len(engine.refresh_calls) < 2 and monotonic() < deadline:
        sleep(0.01)
    coordinator.stop()

    assert len(engine.refresh_calls) >= 2
    assert coordinator.running is False


def test_start_unions_retained_open_position_with_scanner_symbols() -> None:
    transport = FakeTransport()
    retained = ["WYHG"]
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        retained_channels_source=lambda: retained,
    )

    coordinator.start()

    assert coordinator.channels == ("WYHG", "AAA", "BTCUSD")
    assert transport.subscriptions == [("WYHG", "AAA", "BTCUSD")]


def test_scanner_refresh_does_not_remove_retained_symbol() -> None:
    transport = FakeTransport()
    engine = FakeEngine()
    retained = ["WYHG"]
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        retained_channels_source=lambda: retained,
    )
    coordinator.start()
    engine.active_symbols = ()

    coordinator.refresh_universe()

    assert coordinator.channels == ("WYHG",)
    assert transport.subscriptions[-1] == ("WYHG",)


def test_after_hours_maintenance_preserves_retained_management_channels() -> None:
    transport = FakeTransport()
    retained = ["SUNE"]
    coordinator = LiveScannerCoordinator(
        transport, FakeEngine(), retained_channels_source=lambda: retained,
    )
    coordinator.start()
    subscriptions_before = len(transport.subscriptions)

    assert coordinator.ensure_retained_channels() == coordinator.channels
    assert "SUNE" in coordinator.channels
    assert len(transport.subscriptions) == subscriptions_before


def test_position_close_releases_retained_symbol() -> None:
    transport = FakeTransport()
    retained = ["WYHG"]
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        retained_channels_source=lambda: retained,
    )
    coordinator.start()
    retained.clear()

    coordinator.run_once()

    assert coordinator.channels == ("AAA", "BTCUSD")
    assert transport.subscriptions[-1] == ("AAA", "BTCUSD")


def test_position_close_clears_transport_subscription_when_no_scanner_symbols() -> None:
    transport = FakeTransport()
    retained = ["WYHG"]
    engine = FakeEngine()
    engine.active_symbols = ()
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        retained_channels_source=lambda: retained,
    )
    coordinator.start()
    retained.clear()

    coordinator.run_once()

    assert coordinator.channels == ()
    assert transport.subscriptions[-1] == ()


def test_reconnect_recomputes_current_retained_union() -> None:
    transport = FakeTransport()
    retained = ["WYHG"]
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        retained_channels_source=lambda: retained,
    )
    coordinator.start()
    retained.append("SUNE")

    recovered = coordinator.recover_stream()

    assert recovered == ("WYHG", "SUNE", "AAA", "BTCUSD")
    assert transport.subscriptions[-1] == recovered


def test_scanner_and_retained_overlap_is_subscribed_once() -> None:
    transport = FakeTransport()
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        retained_channels_source=lambda: ("AAA", "aaa", "WYHG"),
    )

    coordinator.start()

    assert coordinator.channels == ("AAA", "WYHG", "BTCUSD")
    assert len(coordinator.channels) == len(set(coordinator.channels))


def test_connect_is_idempotent() -> None:
    transport = FakeTransport()
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
    )

    coordinator.connect()
    coordinator.connect()

    assert transport.connect_calls == 1


def test_run_once_processes_one_event() -> None:
    event = FakeEvent("AAA")
    transport = FakeTransport([event])
    engine = FakeEngine()
    engine.decisions["AAA"] = object()

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes",),
    )
    coordinator.start()

    cycle = coordinator.run_once()

    assert cycle.events_read == 1
    assert cycle.decisions_created == 1
    assert cycle.stream_exhausted is False
    assert engine.events == [event]


def test_run_once_reports_empty_stream() -> None:
    coordinator = LiveScannerCoordinator(
        FakeTransport(),
        FakeEngine(),
        default_channels=("quotes",),
    )
    coordinator.start()

    cycle = coordinator.run_once()

    assert cycle.events_read == 0
    assert cycle.decisions_created == 0
    assert cycle.stream_exhausted is True


def test_run_available_drains_current_events() -> None:
    events = [
        FakeEvent("AAA"),
        FakeEvent("BBB"),
        FakeEvent("CCC"),
    ]
    transport = FakeTransport(events)
    engine = FakeEngine()
    engine.decisions["AAA"] = object()
    engine.decisions["CCC"] = object()

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes",),
    )
    coordinator.start()

    cycle = coordinator.run_available()

    assert cycle.events_read == 3
    assert cycle.decisions_created == 2
    assert cycle.stream_exhausted is True


def test_run_available_uses_nonblocking_reads_after_first_event() -> None:
    first = FakeEvent("AAA")
    second = FakeEvent("BBB")

    class DrainAwareTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__([first])
            self.blocking_reads = 0
            self.nonblocking_reads = 0
            self.nowait_events = [second]

        def read_event(self) -> Any | None:
            self.blocking_reads += 1
            return super().read_event()

        def read_event_nowait(self) -> Any | None:
            self.nonblocking_reads += 1
            if not self.nowait_events:
                return None
            return self.nowait_events.pop(0)

    transport = DrainAwareTransport()
    engine = FakeEngine()

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes",),
        maximum_events_per_cycle=100,
    )
    coordinator.start()

    cycle = coordinator.run_available()

    assert cycle.events_read == 2
    assert cycle.stream_exhausted is True
    assert transport.blocking_reads == 1
    assert transport.nonblocking_reads == 2
    assert engine.events == [first, second]


def test_run_available_respects_cycle_limit() -> None:
    transport = FakeTransport(
        [
            FakeEvent("AAA"),
            FakeEvent("BBB"),
            FakeEvent("CCC"),
        ]
    )

    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        default_channels=("quotes",),
        maximum_events_per_cycle=2,
    )
    coordinator.start()

    cycle = coordinator.run_available()

    assert cycle.events_read == 2
    assert cycle.stream_exhausted is False
    assert len(transport.events) == 1


def test_recover_stream_reconnects_without_refreshing_universe() -> None:
    transport = FakeTransport()
    engine = FakeEngine()
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes", "trades"),
    )
    coordinator.start()

    assert transport.connect_calls == 1
    assert transport.subscriptions == [("quotes", "trades")]
    assert len(engine.refresh_calls) == 1

    recovered = coordinator.recover_stream()

    assert recovered == ("quotes", "trades")
    assert transport.disconnect_calls == 1
    assert transport.connect_calls == 2
    assert transport.subscriptions == [
        ("quotes", "trades"),
        ("quotes", "trades"),
    ]
    assert len(engine.refresh_calls) == 1
    assert coordinator.connected is True
    assert coordinator.running is True


def test_recover_stream_preserves_authoritative_lane_continuity() -> None:
    observed: list[FakeEvent] = []
    transport = FakeTransport([FakeEvent("AAA")])
    engine = FakeEngine()
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes",),
        asynchronous_authoritative=True,
        event_observer=observed.append,
    )
    coordinator.start()
    coordinator.run_available()
    coordinator.recover_stream()

    transport.events.append(FakeEvent("AAA"))
    cycle = coordinator.run_available()

    assert cycle.events_read == 1
    deadline = monotonic() + 1.0
    while len(observed) < 2 and monotonic() < deadline:
        sleep(0.001)
    assert len(observed) == 2
    assert coordinator.memory_metrics()["authoritative_lane_failure"] is None
    coordinator.stop()


def test_late_observer_binding_starts_authoritative_lane_after_connect() -> None:
    observed: list[FakeEvent] = []
    stage_started: list[bool] = []

    def observe(event: FakeEvent) -> None:
        stage_started.append(
            performance_diagnostics.snapshot().authoritative_stage.get("stage")
            == "authoritative_worker_event"
        )
        observed.append(event)

    events = [FakeEvent(f"SYM{index:03d}") for index in range(500)]
    transport = FakeTransport(events)
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        default_channels=("quotes",),
        asynchronous_authoritative=True,
    )

    # Match production composition: the transport/coordinator connects before
    # the broker binds its authoritative observer.
    coordinator.connect()
    coordinator.set_event_observer(observe)

    cycle = coordinator.run_transport_available(maximum_events=500)
    assert cycle.events_read == 500

    deadline = monotonic() + 5.0
    while len(observed) < 500 and monotonic() < deadline:
        sleep(0.001)

    metrics = coordinator.memory_metrics()
    assert len(observed) == 500
    assert any(stage_started)
    timing = performance_diagnostics.snapshot().component_timings
    assert timing["authoritative.authoritative_worker_event"]["calls"] >= 500
    assert timing["authoritative_worker_event_complete"]["calls"] >= 500
    assert metrics["authoritative_events_enqueued"] == 500
    assert metrics["authoritative_events_dequeued"] == 500
    assert metrics["authoritative_lane_depth"] == 0
    assert metrics["authoritative_lane_overflow"] == 0
    assert [event.symbol for event in observed] == [
        f"SYM{index:03d}" for index in range(500)
    ]
    coordinator.disconnect()


def test_late_observer_binding_reconnects_without_duplicate_lane_workers() -> None:
    first: list[FakeEvent] = []
    second: list[FakeEvent] = []
    transport = FakeTransport([FakeEvent("FIRST"), FakeEvent("SECOND")])
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        default_channels=("quotes",),
        asynchronous_authoritative=True,
    )

    coordinator.connect()
    coordinator.set_event_observer(first.append)
    coordinator.set_event_observer(second.append)
    coordinator.run_transport_available(maximum_events=1)
    deadline = monotonic() + 2.0
    while len(second) < 1 and monotonic() < deadline:
        sleep(0.001)
    assert first == []
    assert [event.symbol for event in second] == ["FIRST"]

    coordinator.disconnect()
    coordinator.connect()
    coordinator.run_transport_available(maximum_events=1)
    deadline = monotonic() + 2.0
    while len(second) < 2 and monotonic() < deadline:
        sleep(0.001)
    assert [event.symbol for event in second] == ["FIRST", "SECOND"]
    assert coordinator.memory_metrics()["authoritative_lane_overflow"] == 0
    coordinator.disconnect()


def test_recover_stream_fails_closed_when_authoritative_worker_failed() -> None:
    transport = FakeTransport([FakeEvent("AAA")])
    engine = FakeEngine()

    def fail(_event: FakeEvent) -> None:
        raise RuntimeError("observer failed")

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes",),
        asynchronous_authoritative=True,
        event_observer=fail,
    )
    coordinator.start()
    coordinator.run_available()
    deadline = monotonic() + 1.0
    while coordinator.memory_metrics()["authoritative_lane_failure"] is None:
        if monotonic() >= deadline:
            raise AssertionError("authoritative worker did not fail")
        sleep(0.001)

    with pytest.raises(RuntimeError, match="authoritative lane worker failed"):
        coordinator.recover_stream()

    assert coordinator.connected is False
    assert coordinator.running is False


def test_recover_stream_requires_existing_subscription() -> None:
    coordinator = LiveScannerCoordinator(
        FakeTransport(),
        FakeEngine(),
        default_channels=("quotes",),
    )

    with pytest.raises(
        RuntimeError,
        match="requires an active subscription",
    ):
        coordinator.recover_stream()


def test_recover_stream_disconnects_after_resubscribe_failure() -> None:
    class FailingRecoveryTransport(FakeTransport):
        def subscribe(self, channels: tuple[str, ...]) -> None:
            super().subscribe(channels)
            if self.connect_calls > 1:
                raise RuntimeError("resubscribe failed")

    transport = FailingRecoveryTransport()
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        default_channels=("quotes",),
    )
    coordinator.start()

    with pytest.raises(RuntimeError, match="resubscribe failed"):
        coordinator.recover_stream()

    assert transport.disconnect_calls == 2
    assert coordinator.connected is False
    assert coordinator.running is False


def test_stop_prevents_additional_processing() -> None:
    coordinator = LiveScannerCoordinator(
        FakeTransport([FakeEvent("AAA")]),
        FakeEngine(),
        default_channels=("quotes",),
    )
    coordinator.start()
    coordinator.stop()

    with pytest.raises(
        RuntimeError,
        match="not running",
    ):
        coordinator.run_once()


def test_disconnect_resets_running_state() -> None:
    transport = FakeTransport()
    coordinator = LiveScannerCoordinator(
        transport,
        FakeEngine(),
        default_channels=("quotes",),
    )
    coordinator.start()

    coordinator.disconnect()
    status = coordinator.status()

    assert status.connected is False
    assert status.running is False
    assert transport.disconnect_calls == 1


def test_status_tracks_runtime_totals() -> None:
    transport = FakeTransport(
        [
            FakeEvent("AAA"),
            FakeEvent("BBB"),
        ]
    )
    engine = FakeEngine()
    engine.decisions["AAA"] = object()

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes",),
    )
    coordinator.start()
    coordinator.run_available()

    status = coordinator.status()

    assert status.cycles_completed == 1
    assert status.events_read == 2
    assert status.decisions_created == 1
    assert status.channels == ("quotes",)


def test_snapshot_is_forwarded_to_engine() -> None:
    engine = FakeEngine()
    coordinator = LiveScannerCoordinator(
        FakeTransport(),
        engine,
    )

    result = coordinator.snapshot(limit=12)

    assert result == {"limit": 12}
    assert engine.snapshot_limits == [12]


def test_context_manager_disconnects() -> None:
    transport = FakeTransport()

    with LiveScannerCoordinator(
        transport,
        FakeEngine(),
    ) as coordinator:
        assert coordinator.connected is True

    assert transport.connected is False
    assert transport.disconnect_calls == 1


def test_iter_available_yields_incrementally() -> None:
    first = FakeEvent("AAA")
    second = FakeEvent("BBB")
    transport = FakeTransport([first, second])

    result = tuple(iter_available(transport))

    assert result == (first, second)


def test_empty_channel_collection_is_rejected() -> None:
    coordinator = LiveScannerCoordinator(
        FakeTransport(),
        FakeEngine(),
    )
    coordinator.connect()

    with pytest.raises(
        ValueError,
        match="at least one channel",
    ):
        coordinator.subscribe()


def test_invalid_cycle_limit_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="must be positive",
    ):
        LiveScannerCoordinator(
            FakeTransport(),
            FakeEngine(),
            maximum_events_per_cycle=0,
        )


def test_coordinator_has_no_execution_methods() -> None:
    method_names = set(
        LiveScannerCoordinator.__dict__
    )

    assert "submit_order" not in method_names
    assert "place_order" not in method_names
    assert "cancel_order" not in method_names



def test_recover_stream_resets_engine_state_before_reconnecting() -> None:
    actions = []

    class RecoveryTransport(FakeTransport):
        def connect(self):
            actions.append("connect")
            super().connect()

        def disconnect(self):
            actions.append("disconnect")
            super().disconnect()

        def subscribe(self, channels):
            actions.append("subscribe")
            super().subscribe(channels)

    class ResettableEngine(FakeEngine):
        def reset_stream_state(self):
            actions.append("reset")
            return ("AAA", "BBB")

    transport = RecoveryTransport()
    engine = ResettableEngine()

    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        default_channels=("quotes", "trades"),
    )

    coordinator.start()

    actions.clear()

    recovered = coordinator.recover_stream()

    assert recovered == ("quotes", "trades")
    assert actions == [
        "disconnect",
        "reset",
        "connect",
        "subscribe",
    ]


def test_long_universe_rotation_stays_bounded_and_retains_positions() -> None:
    transport = FakeTransport()
    engine = FakeEngine()
    retained = ["RISK"]
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        retained_channels_source=lambda: retained,
        maximum_subscription_channels=5,
    )
    coordinator.start()

    observed_symbols = set(coordinator.channels)
    for rotation in range(150):
        engine.active_symbols = tuple(
            f"S{rotation:03d}{offset:02d}" for offset in range(10)
        )
        coordinator.refresh_universe()
        observed_symbols.update(coordinator.channels)
        assert len(coordinator.channels) == 5
        assert "RISK" in coordinator.channels

    assert len(observed_symbols) > 100
    assert all(len(channels) <= 5 for channels in transport.subscriptions)


def test_retained_position_overflow_fails_closed() -> None:
    coordinator = LiveScannerCoordinator(
        FakeTransport(),
        FakeEngine(),
        retained_channels_source=lambda: ("A", "B"),
        maximum_subscription_channels=1,
    )

    with pytest.raises(RuntimeError, match="retained position channels exceed"):
        coordinator.start()

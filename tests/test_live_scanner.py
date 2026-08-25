from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.live_scanner import (
    LiveScannerCoordinator,
    ReceiveTransportAdapter,
)
from app.market_data.stream import iter_available
from app.momentum_scanner import AssetClass


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

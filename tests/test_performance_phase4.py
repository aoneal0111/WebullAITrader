"""Deterministic startup/feed-readiness tests."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from time import monotonic

from app.live_scanner.coordinator import LiveScannerCoordinator
from app.momentum_scanner import AssetClass
from app.performance_diagnostics import performance_diagnostics
from app.realtime_scanner.engine import RealtimeScannerEngine
from app.universe import SecurityType, UniverseSelection, UniverseSymbol


@dataclass(frozen=True)
class _Event:
    symbol: str


class _Transport:
    def __init__(self) -> None:
        self.events: list[_Event] = []
        self.connected = False
        self.subscriptions: list[tuple[str, ...]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, channels: tuple[str, ...]) -> None:
        self.subscriptions.append(channels)

    def read_event(self) -> _Event | None:
        return self.events.pop(0) if self.events else None


class _AsyncEngine:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.events: list[_Event] = []
        self.active_symbols = ()
        self.pending_reference_symbols = ("DBGI",)
        self.subscription_symbols = ("DBGI",)

    def prepare_universe(self, asset_classes):
        return self.subscription_symbols

    def refresh_universe(self, asset_classes, *, force_reference_refresh=False):
        self.started.set()
        self.release.wait(2.0)
        self.active_symbols = ("DBGI",)
        self.pending_reference_symbols = ()
        return self.active_symbols

    def consume(self, event):
        self.events.append(event)
        return None

    def snapshot(self, *, limit=25):
        return type("Snapshot", (), {"active_symbols": self.active_symbols})()

    def close(self):
        pass


class _IncrementalEngine(_AsyncEngine):
    def __init__(self) -> None:
        super().__init__()
        self.active_symbols = ()
        self.pending_reference_symbols = ("A", "B")
        self.subscription_symbols = ("A", "B")
        self.first_ready = Event()
        self.second_started = Event()
        self.release_second = Event()
        self.reference_ready_observer = None

    def set_reference_ready_observer(self, observer):
        self.reference_ready_observer = observer

    def refresh_universe(self, asset_classes, *, force_reference_refresh=False):
        self.active_symbols = ("A",)
        self.pending_reference_symbols = ("B",)
        self.first_ready.set()
        if self.reference_ready_observer is not None:
            self.reference_ready_observer()
        self.second_started.set()
        self.release_second.wait(2.0)
        self.active_symbols = ("A", "B")
        self.pending_reference_symbols = ()
        return self.active_symbols


def test_reference_warmup_does_not_block_callback_consumption() -> None:
    transport = _Transport()
    engine = _AsyncEngine()
    coordinator = LiveScannerCoordinator(transport, engine)

    started = monotonic()
    coordinator.start(asset_classes=(AssetClass.STOCK,))
    startup_elapsed = monotonic() - started
    assert startup_elapsed < 0.5
    assert engine.started.wait(0.5)

    transport.events.append(_Event("DBGI"))
    coordinator.run_once()
    assert engine.events == [_Event("DBGI")]
    engine.release.set()
    coordinator.stop()


def test_coordinator_forwards_pending_references_and_observation_readiness() -> None:
    transport = _Transport()
    engine = _AsyncEngine()
    coordinator = LiveScannerCoordinator(transport, engine)

    coordinator.start(asset_classes=(AssetClass.STOCK,))
    assert coordinator.pending_reference_symbols == ("DBGI",)
    assert coordinator.observation_ready is True
    assert coordinator.qualification_ready is False
    engine.release.set()
    coordinator.stop()


def test_reference_completion_promotes_readiness_without_resubscription() -> None:
    transport = _Transport()
    engine = _AsyncEngine()
    coordinator = LiveScannerCoordinator(transport, engine)
    promoted = Event()
    coordinator.set_readiness_observer(promoted.set)

    coordinator.start(asset_classes=(AssetClass.STOCK,))
    subscriptions_before = len(transport.subscriptions)
    engine.release.set()
    assert promoted.wait(1.0)
    assert coordinator.qualification_ready is True
    assert len(transport.subscriptions) == subscriptions_before
    coordinator.stop()


def test_retained_position_channel_keeps_observation_ready_without_candidates() -> None:
    transport = _Transport()
    engine = _AsyncEngine()
    engine.pending_reference_symbols = ()
    engine.subscription_symbols = ()
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        retained_channels_source=lambda: ("DBGI",),
    )

    assert coordinator.start(asset_classes=(AssetClass.STOCK,)) == ()
    assert coordinator.observation_ready is True
    assert coordinator.channels == ("DBGI",)
    coordinator.stop()


def test_reference_ready_observer_promotes_each_symbol_without_waiting_for_all() -> None:
    transport = _Transport()
    engine = _IncrementalEngine()
    coordinator = LiveScannerCoordinator(transport, engine)
    promotions: list[tuple[str, ...]] = []
    coordinator.set_readiness_observer(
        lambda: promotions.append(coordinator.active_symbols)
    )

    coordinator.start(asset_classes=(AssetClass.STOCK,))
    assert engine.first_ready.wait(1.0)
    assert promotions == [("A",)]
    assert coordinator.pending_reference_symbols == ("B",)
    assert coordinator.qualification_ready is True
    assert engine.second_started.is_set() is True
    engine.release_second.set()
    coordinator.stop()


def test_reference_total_is_not_double_counted_for_prepared_selection() -> None:
    symbol = UniverseSymbol(
        "DBGI", AssetClass.STOCK, "NASDAQ", SecurityType.COMMON_STOCK, True
    )

    class Universe:
        def select_all(self, asset_classes):
            return UniverseSelection((symbol,), ())

    class References:
        def get_for_instrument(self, item, *, force_refresh=False):
            return type("Reference", (), {"as_of": None})()

    class Pipeline:
        def consume(self, event):
            return None

    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        performance_diagnostics.start_run(
            artifact_path=f"{directory}/performance.json",
            flush_seconds=60.0,
        )
        engine = RealtimeScannerEngine(Universe(), References(), Pipeline())
        engine.prepare_universe((AssetClass.STOCK,))
        engine.refresh_universe((AssetClass.STOCK,))
        assert performance_diagnostics.startup_metrics()[
            "reference_warmup_symbols_total"
        ] == 1
        performance_diagnostics.finish_run()


def test_real_engine_exposes_pending_references_without_qualifying_them() -> None:
    symbol = UniverseSymbol(
        "DBGI", AssetClass.STOCK, "NASDAQ", SecurityType.COMMON_STOCK, True
    )

    class Universe:
        def select_all(self, asset_classes):
            return UniverseSelection((symbol,), ())

    class References:
        def get_for_instrument(self, item, *, force_refresh=False):
            raise RuntimeError("controlled pending reference")

    class Pipeline:
        def consume(self, event):
            return None

    engine = RealtimeScannerEngine(Universe(), References(), Pipeline())
    assert engine.prepare_universe((AssetClass.STOCK,)) == ("DBGI",)
    assert engine.active_symbols == ()
    assert engine.pending_reference_symbols == ("DBGI",)
    assert engine.snapshot().healthy is False


def test_generation_state_starts_clean_after_reconnect() -> None:
    from app.webull.websocket_client import OfficialSdkStreamBackend

    class Client:
        def __init__(self, session_id):
            self._client_id = session_id
            self.api_client = object()
            self.on_quotes_message = None
            self.on_connect_success = None
            self.on_disconnect = None

        def connect_and_loop_start(self, **kwargs):
            self.on_connect_success(self, self.api_client, self._client_id)

        def subscribe(self, **kwargs):
            pass

        def disconnect(self):
            pass

        def loop_stop(self):
            pass

    first = Client("one")
    second = Client("two")
    backend = OfficialSdkStreamBackend(
        first,
        subscription_mapper=lambda channels: {"symbols": channels},
        connect_timeout_seconds=0.1,
        registration_grace_seconds=0,
        sleeper=lambda _: None,
        sdk_client_factory=lambda: second,
    )
    backend.connect()
    assert backend.generation_metrics["generation"] == 1
    first_generation = backend.generation_metrics["generation"]
    backend.disconnect()
    backend.connect()
    assert backend.generation_metrics["generation"] == first_generation + 1
    assert backend.generation_metrics["first_raw_callback_at"] is None
    assert backend.generation_metrics["first_callback_dequeued_at"] is None

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Event, Lock, Thread
from time import monotonic, sleep

from app.live_scanner.coordinator import LiveScannerCoordinator


@dataclass(frozen=True)
class _Event:
    symbol: str
    received_at: float


class _Transport:
    def __init__(self) -> None:
        self.events: deque[_Event] = deque()
        self.lock = Lock()
        self.wakeup = Event()
        self.connected = False
        self.max_depth = 0

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, channels: tuple[str, ...]) -> None:
        return None

    def read_event(self) -> _Event | None:
        with self.lock:
            return self.events.popleft() if self.events else None

    def read_event_nowait(self) -> _Event | None:
        with self.lock:
            return self.events.popleft() if self.events else None

    def put(self, event: _Event) -> None:
        with self.lock:
            self.events.append(event)
            self.max_depth = max(self.max_depth, len(self.events))
        self.wakeup.set()


class _Engine:
    def __init__(self) -> None:
        self.events: list[_Event] = []

    def prepare_universe(self, asset_classes):
        return ("WHLR", "IPDN", "LXEH")

    def refresh_universe(self, asset_classes, *, force_reference_refresh=False):
        return ("WHLR", "IPDN", "LXEH")

    @property
    def subscription_symbols(self):
        return ("WHLR", "IPDN", "LXEH")

    def consume(self, event: _Event):
        # Representative canonical-reduction cost; all accepted events still
        # reach the reducer before any downstream supersession is possible.
        sleep(0.002)
        self.events.append(event)
        return None

    def snapshot(self, *, limit=25):
        return ()


def test_sustained_market_burst_keeps_raw_service_current() -> None:
    transport = _Transport()
    engine = _Engine()
    coordinator = LiveScannerCoordinator(
        transport,
        engine,
        maximum_events_per_cycle=100,
        retained_channels_source=lambda: (),
    )
    coordinator.start()

    total = 600
    stop = Event()

    def produce() -> None:
        for index in range(total):
            if stop.is_set():
                return
            symbol = "WHLR" if index % 2 else ("IPDN" if index % 6 else "LXEH")
            transport.put(_Event(symbol, monotonic()))
            sleep(1.0 / 68.0)

    producer = Thread(target=produce)
    producer.start()
    ages: list[float] = []
    try:
        while producer.is_alive() or len(engine.events) < total:
            cycle = coordinator.run_available()
            # The event timestamp is captured before enqueue; this is the
            # raw-ingress-to-reducer residence measured by the replay.
            if cycle.events_read:
                for event in engine.events[-cycle.events_read:]:
                    ages.append(monotonic() - event.received_at)
            if not cycle.events_read:
                sleep(0.001)
    finally:
        stop.set()
        producer.join(2.0)
        coordinator.disconnect()

    ages.sort()
    p99 = ages[min(len(ages) - 1, int(len(ages) * 0.99))]
    assert len(engine.events) == total
    assert transport.max_depth < 4096
    assert p99 < 5.0

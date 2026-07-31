from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.live_scanner.models import (
    LiveScannerCycle,
    LiveScannerStatus,
)
from app.live_scanner.protocols import (
    LiveScannerEngine,
    SubscribableMarketDataTransport,
)
from app.momentum_scanner import AssetClass


class LiveScannerCoordinator:
    """
    Coordinates stream lifecycle and incremental event delivery.

    Responsibilities:
    - connect and disconnect the transport
    - subscribe to normalized channel names
    - refresh the scanner universe
    - deliver events to RealtimeScannerEngine
    - expose scanner snapshots and runtime counters

    This class does not submit or manage orders.
    """

    def __init__(
        self,
        transport: SubscribableMarketDataTransport,
        engine: LiveScannerEngine,
        *,
        default_channels: Iterable[str] = (),
        maximum_events_per_cycle: int = 1000,
        event_observer: Callable[[Any], object] | None = None,
    ) -> None:
        if maximum_events_per_cycle <= 0:
            raise ValueError(
                "maximum_events_per_cycle must be positive"
            )

        self._transport = transport
        self._engine = engine
        self._default_channels = _normalize_channels(
            default_channels
        )
        self._maximum_events_per_cycle = (
            maximum_events_per_cycle
        )
        if event_observer is not None and not callable(event_observer):
            raise TypeError("event_observer must be callable or None")
        self._event_observer = event_observer

        self._channels: tuple[str, ...] = ()
        self._connected = False
        self._running = False
        self._cycles_completed = 0
        self._events_read = 0
        self._decisions_created = 0

    def connect(self) -> None:
        if self._connected:
            return

        self._transport.connect()
        self._connected = True

    def disconnect(self) -> None:
        if not self._connected:
            self._running = False
            return

        try:
            self._transport.disconnect()
        finally:
            self._connected = False
            self._running = False

    def subscribe(
        self,
        channels: Iterable[str] | None = None,
    ) -> tuple[str, ...]:
        self._require_connected()

        selected = (
            self._default_channels
            if channels is None
            else _normalize_channels(channels)
        )

        if not selected:
            raise ValueError(
                "at least one channel is required"
            )

        self._transport.subscribe(selected)
        self._channels = selected
        return selected

    def refresh_universe(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
        *,
        force_reference_refresh: bool = False,
    ) -> tuple[str, ...]:
        return self._engine.refresh_universe(
            asset_classes,
            force_reference_refresh=(
                force_reference_refresh
            ),
        )

    def start(
        self,
        *,
        channels: Iterable[str] | None = None,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
        force_reference_refresh: bool = False,
    ) -> tuple[str, ...]:
        self.connect()
        active_symbols = self.refresh_universe(
            asset_classes,
            force_reference_refresh=(
                force_reference_refresh
            ),
        )
        if not active_symbols:
            # Keep the connected runtime responsive and publish the engine's
            # explicit empty fail-closed snapshot. No subscription is sent.
            self._channels = ()
            self._running = True
            return ()
        selected_channels = (
            self._default_channels
            or getattr(
                self._engine,
                "subscription_symbols",
                active_symbols,
            )
            if channels is None
            else channels
        )
        self.subscribe(selected_channels)

        self._running = True
        return active_symbols

    def stop(self) -> None:
        self._running = False

    def run_once(self) -> LiveScannerCycle:
        self._require_running()

        event = self._transport.read_event()

        if event is None:
            self._cycles_completed += 1
            return LiveScannerCycle(
                events_read=0,
                decisions_created=0,
                stream_exhausted=True,
                running=self._running,
            )

        decision = self._consume(event)

        self._events_read += 1
        self._cycles_completed += 1

        decisions_created = int(decision is not None)
        self._decisions_created += decisions_created

        return LiveScannerCycle(
            events_read=1,
            decisions_created=decisions_created,
            stream_exhausted=False,
            running=self._running,
        )

    def run_available(
        self,
        *,
        maximum_events: int | None = None,
    ) -> LiveScannerCycle:
        self._require_running()

        limit = (
            self._maximum_events_per_cycle
            if maximum_events is None
            else maximum_events
        )

        if limit <= 0:
            raise ValueError(
                "maximum_events must be positive"
            )

        events_read = 0
        decisions_created = 0
        stream_exhausted = False

        while self._running and events_read < limit:
            event = self._transport.read_event()

            if event is None:
                stream_exhausted = True
                break

            decision = self._consume(event)
            events_read += 1

            if decision is not None:
                decisions_created += 1

        self._events_read += events_read
        self._decisions_created += decisions_created
        self._cycles_completed += 1

        return LiveScannerCycle(
            events_read=events_read,
            decisions_created=decisions_created,
            stream_exhausted=stream_exhausted,
            running=self._running,
        )

    def snapshot(
        self,
        *,
        limit: int = 25,
    ) -> Any:
        return self._engine.snapshot(limit=limit)

    def status(self) -> LiveScannerStatus:
        return LiveScannerStatus(
            connected=self._connected,
            running=self._running,
            channels=self._channels,
            cycles_completed=self._cycles_completed,
            events_read=self._events_read,
            decisions_created=self._decisions_created,
        )

    def close(self) -> None:
        self.disconnect()

    def set_event_observer(
        self,
        observer: Callable[[Any], object] | None,
    ) -> None:
        if observer is not None and not callable(observer):
            raise TypeError("event observer must be callable or None")
        self._event_observer = observer

    def __enter__(self) -> LiveScannerCoordinator:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        self.disconnect()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def running(self) -> bool:
        return self._running

    @property
    def channels(self) -> tuple[str, ...]:
        return self._channels

    @property
    def heartbeat_ok(self) -> bool:
        return bool(getattr(self._transport, "heartbeat_ok", False))

    @property
    def subscription_acknowledged(self) -> bool:
        return bool(
            getattr(self._transport, "subscription_acknowledged", False)
        )

    @property
    def reconnect_ready(self) -> bool:
        return bool(getattr(self._transport, "reconnect_ready", False))

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError(
                "live scanner is not connected"
            )

    def _require_running(self) -> None:
        if not self._connected:
            raise RuntimeError(
                "live scanner is not connected"
            )

        if not self._running:
            raise RuntimeError(
                "live scanner is not running"
            )

    def _consume(self, event: Any) -> Any:
        decision = self._engine.consume(event)
        if self._event_observer is not None:
            self._event_observer(event)
        return decision


def _normalize_channels(
    channels: Iterable[str],
) -> tuple[str, ...]:
    normalized = {
        str(channel).strip()
        for channel in channels
        if str(channel).strip()
    }

    return tuple(sorted(normalized))

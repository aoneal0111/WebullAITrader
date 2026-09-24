from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Any

from app.live_scanner.models import (
    LiveScannerCycle,
    LiveScannerStatus,
)
from app.live_scanner.authoritative_lane import AuthoritativeEventLane
from app.live_scanner.protocols import (
    LiveScannerEngine,
    SubscribableMarketDataTransport,
)
from app.momentum_scanner import AssetClass
from app.performance_diagnostics import performance_diagnostics


# Official SDK connection is bounded at 10 seconds, followed by bounded
# registration/subscription work. Recovery gets one composed bounded window so
# a normal slow disconnect is not misclassified as an immediate retry.
RECOVERY_TRANSPORT_TIMEOUT_SECONDS = 15.0


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
        retained_channels_source: Callable[[], Iterable[str]] | None = None,
        universe_refresh_interval_seconds: float = 60.0,
        maximum_subscription_channels: int = 100,
        asynchronous_authoritative: bool = False,
        authoritative_lane_capacity: int = 4096,
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
        self._asynchronous_authoritative = bool(asynchronous_authoritative)
        if authoritative_lane_capacity <= 0:
            raise ValueError("authoritative lane capacity must be positive")
        self._authoritative_lane_capacity = int(authoritative_lane_capacity)
        self._authoritative_lane: AuthoritativeEventLane | None = None
        self._event_observer = event_observer
        if self._asynchronous_authoritative and event_observer is not None:
            self._authoritative_lane = self._new_authoritative_lane()
        if retained_channels_source is not None and not callable(retained_channels_source):
            raise TypeError("retained channels source must be callable or None")
        self._retained_channels_source = retained_channels_source
        if universe_refresh_interval_seconds < 0:
            raise ValueError("universe refresh interval cannot be negative")
        self._universe_refresh_interval_seconds = float(universe_refresh_interval_seconds)
        if maximum_subscription_channels <= 0:
            raise ValueError("maximum subscription channels must be positive")
        self._maximum_subscription_channels = int(maximum_subscription_channels)
        self._evaluation_stop = Event()
        self._evaluation_thread: Thread | None = None

        self._channels: tuple[str, ...] = ()
        self._scanner_channels: tuple[str, ...] = self._default_channels
        self._connected = False
        self._running = False
        self._cycles_completed = 0
        self._events_read = 0
        self._decisions_created = 0
        self._reference_stop = Event()
        self._reference_thread: Thread | None = None
        self._universe_refresh_stop = Event()
        self._universe_refresh_thread: Thread | None = None
        self._universe_refresh_asset_classes: tuple[AssetClass, ...] = ()
        self._universe_refresh_call_lock = Lock()
        self._readiness_observer: Callable[[], object] | None = None
        self._last_failure_stage = "IDLE"
        self._subscription_bootstrap_pending = True

    def connect(self) -> None:
        if self._connected:
            return

        self._transport.connect()
        self._connected = True
        self._start_background_workers()

    def disconnect(
        self,
        *,
        preserve_authoritative_lane: bool = False,
        transport_timeout_seconds: float | None = None,
    ) -> None:
        if not self._connected:
            self._running = False
            if not preserve_authoritative_lane:
                self._stop_background_workers()
                self._subscription_bootstrap_pending = True
            return

        try:
            self._stop_background_workers(
                stop_authoritative=not preserve_authoritative_lane,
            )
            disconnect = getattr(self._transport, "disconnect")
            if transport_timeout_seconds is None:
                disconnect()
            else:
                try:
                    disconnect(timeout_seconds=transport_timeout_seconds)
                except TypeError:
                    disconnect()
        finally:
            self._stop_universe_refresh()
            self._connected = False
            self._running = False
            if not preserve_authoritative_lane:
                self._subscription_bootstrap_pending = True

    def subscribe(
        self,
        channels: Iterable[str] | None = None,
    ) -> tuple[str, ...]:
        self._require_connected()

        scanner_channels = (
            self._default_channels
            if channels is None
            else _normalize_channels(channels)
        )
        self._scanner_channels = scanner_channels
        if (
            self._subscription_bootstrap_pending
            and self._channels
            and len(scanner_channels) <= 1
        ):
            return self._channels
        if len(scanner_channels) > 1:
            self._subscription_bootstrap_pending = False
        return self._subscribe_effective(scanner_channels)

    def refresh_universe(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
        *,
        force_reference_refresh: bool = False,
    ) -> tuple[str, ...]:
        # Manual maintenance calls and the periodic worker share one bounded
        # critical section; a slow provider cannot spawn overlapping warmups.
        with self._universe_refresh_call_lock:
            active_symbols = self._engine.refresh_universe(
                asset_classes,
                force_reference_refresh=(
                    force_reference_refresh
                ),
            )
            self._scanner_channels = _normalize_channels(
                getattr(self._engine, "subscription_symbols", active_symbols)
            )
            if self._running:
                self._sync_subscription()
            return active_symbols

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
        prepare = getattr(self._engine, "prepare_universe", None)
        if callable(prepare) and channels is None:
            pending_channels = _normalize_channels(prepare(asset_classes))
            selected_channels = pending_channels or self._effective_channels(
                self._default_channels
            )
            if not selected_channels:
                self._scanner_channels = ()
                self._channels = ()
                self._running = True
                return pending_channels
            self._scanner_channels = pending_channels
            self._subscribe_effective(pending_channels)
            self._subscription_bootstrap_pending = len(self._channels) <= 1
            self._running = True
            self._reference_stop.clear()
            self._reference_thread = Thread(
                target=self._complete_reference_warmup,
                args=(asset_classes, force_reference_refresh),
                name="realtime-reference-warmup",
                daemon=True,
            )
            self._reference_thread.start()
            self._start_universe_refresh(asset_classes)
            return pending_channels
        active_symbols = self.refresh_universe(
            asset_classes,
            force_reference_refresh=(
                force_reference_refresh
            ),
        )
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
        selected_channels = _normalize_channels(selected_channels)
        if not self._effective_channels(selected_channels):
            # Keep the connected runtime responsive when neither the scanner
            # nor an active management lifecycle requires a subscription.
            self._scanner_channels = ()
            self._channels = ()
            self._running = True
            return ()
        self.subscribe(selected_channels)
        self._subscription_bootstrap_pending = len(self._channels) <= 1

        self._running = True
        if channels is None:
            self._start_universe_refresh(asset_classes)
        return active_symbols

    def stop(self) -> None:
        self._running = False
        self._reference_stop.set()
        self._stop_universe_refresh()
        self._stop_background_workers()
        thread = self._reference_thread
        if thread is not None:
            thread.join(2.0)
            self._reference_thread = None
        close = getattr(self._engine, "close", None)
        if callable(close):
            close()

    def set_readiness_observer(
        self,
        observer: Callable[[], object] | None,
    ) -> None:
        if observer is not None and not callable(observer):
            raise TypeError("readiness observer must be callable or None")
        self._readiness_observer = observer
        setter = getattr(self._engine, "set_reference_ready_observer", None)
        if callable(setter):
            setter(self._notify_readiness)

    def recover_stream(self) -> tuple[str, ...]:
        """Reconnect the transport and restore the current subscription."""
        if not self._channels:
            raise RuntimeError(
                "scanner stream recovery requires an active subscription"
            )

        # A stream reconnect is recoverable transport churn, not an
        # authoritative-session shutdown.  Keep the ordered lane alive so
        # accepted events retain one continuous delivery path and so its
        # worker's drain cannot stall the market-data consumer for tens of
        # seconds while the feed is being re-established.
        self.disconnect(
            preserve_authoritative_lane=True,
            transport_timeout_seconds=RECOVERY_TRANSPORT_TIMEOUT_SECONDS,
        )

        reset_stream_state = getattr(
            self._engine,
            "reset_stream_state",
            None,
        )
        if callable(reset_stream_state):
            reset_stream_state()

        try:
            self.connect()
            restored = self._subscribe_effective(self._scanner_channels)
        except Exception:
            self.disconnect()
            raise

        self._running = True
        return restored

    def run_once(self) -> LiveScannerCycle:
        self._require_running()

        event = self._transport.read_event()

        if event is None:
            self._sync_subscription()
            self._cycles_completed += 1
            return LiveScannerCycle(
                events_read=0,
                decisions_created=0,
                stream_exhausted=True,
                running=self._running,
            )

        decision = self._consume(event)
        drained = self._drain_evaluations()
        self._sync_subscription()

        self._events_read += 1
        self._cycles_completed += 1

        decisions_created = int(decision is not None) + len(drained)
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
            if events_read == 0:
                self._last_failure_stage = "RAW_RECEIVE"
                event = self._transport.read_event()
            else:
                read_nowait = getattr(
                    self._transport,
                    "read_event_nowait",
                    None,
                )
                event = (
                    read_nowait()
                    if callable(read_nowait)
                    else self._transport.read_event()
                )

            if event is None:
                self._sync_subscription()
                stream_exhausted = True
                break

            decision = self._consume(event)
            events_read += 1

            if decision is not None:
                decisions_created += 1

        # Subscription reconciliation is batch-scoped.  Calling the retained
        # position source for every raw event puts unrelated GUI/report locks
        # directly on the ingress hot path.  The bounded batch still checks
        # management channels before the next receive cycle, while every
        # accepted event has already passed canonical reduction and
        # authoritative admission.
        self._last_failure_stage = "SUBSCRIPTION_SYNC"
        self._sync_subscription()

        drained = self._drain_evaluations()
        decisions_created += len(drained)

        self._events_read += events_read
        self._decisions_created += decisions_created
        self._cycles_completed += 1

        return LiveScannerCycle(
            events_read=events_read,
            decisions_created=decisions_created,
            stream_exhausted=stream_exhausted,
            running=self._running,
        )

    def run_transport_available(
        self,
        *,
        maximum_events: int | None = None,
    ) -> LiveScannerCycle:
        """Drain transport callbacks before scanner admission is ready."""
        if not self._connected:
            raise RuntimeError("live scanner is not connected")
        limit = self._maximum_events_per_cycle if maximum_events is None else maximum_events
        if limit <= 0:
            raise ValueError("maximum_events must be positive")
        events_read = 0
        while events_read < limit:
            read_nowait = getattr(self._transport, "read_event_nowait", None)
            event = read_nowait() if callable(read_nowait) else self._transport.read_event()
            if event is None:
                break
            reducer = getattr(self._engine, "consume_transport_only", None)
            if callable(reducer):
                reducer(event)
            if self._authoritative_lane is not None:
                self._authoritative_lane.publish(event)
            elif self._event_observer is not None:
                self._event_observer(event)
            events_read += 1
        self._events_read += events_read
        self._cycles_completed += 1
        return LiveScannerCycle(
            events_read=events_read,
            decisions_created=0,
            stream_exhausted=events_read < limit,
            running=self._running,
        )

    def _drain_evaluations(self) -> tuple[Any, ...]:
        if self._asynchronous_authoritative:
            return ()
        drain = getattr(self._engine, "drain_evaluations", None)
        if not callable(drain):
            return ()
        return tuple(drain())

    def snapshot(
        self,
        *,
        limit: int = 25,
    ) -> Any:
        return self._engine.snapshot(limit=limit)

    def diagnostic_results(self, *, limit: int = 3):
        diagnostics = getattr(self._engine, "diagnostic_results", None)
        return () if not callable(diagnostics) else diagnostics(limit=limit)

    def qualification_diagnostics(self, *, example_limit: int = 3):
        diagnostics = getattr(self._engine, "qualification_diagnostics", None)
        return (
            None
            if not callable(diagnostics)
            else diagnostics(example_limit=example_limit)
        )

    def status(self) -> LiveScannerStatus:
        return LiveScannerStatus(
            connected=self._connected,
            running=self._running,
            channels=self._channels,
            cycles_completed=self._cycles_completed,
            events_read=self._events_read,
            decisions_created=self._decisions_created,
        )

    def memory_metrics(self) -> dict[str, object]:
        """Expose bounded transport, reducer, and mailbox diagnostics."""
        transport_metrics = getattr(self._transport, "memory_metrics", None)
        transport_latency = getattr(self._transport, "latency_metrics", None)
        transport_ingestion = getattr(self._transport, "ingestion_metrics", None)
        transport_discard = getattr(self._transport, "discard_metrics", None)
        transport_generation_accounting = getattr(self._transport, "generation_accounting", None)
        engine_metrics = getattr(self._engine, "memory_metrics", None)
        metrics = {
            "last_failure_stage": self._last_failure_stage,
            **({} if not callable(transport_metrics) else {
                f"transport_{key}": value
                for key, value in transport_metrics().items()
            }),
            **({} if not callable(engine_metrics) else {
                f"engine_{key}": value
                for key, value in engine_metrics().items()
            }),
        }
        if callable(transport_latency):
            metrics.update({
                f"transport_{key}": value
                for key, value in transport_latency().items()
            })
        if callable(transport_ingestion):
            metrics.update({
                f"transport_{key}": value
                for key, value in transport_ingestion().items()
            })
        if callable(transport_discard):
            metrics.update({
                f"transport_{key}": value
                for key, value in transport_discard().items()
            })
        if callable(transport_generation_accounting):
            metrics["transport_generation_accounting"] = transport_generation_accounting()
        if "transport_messages_enqueued" in metrics:
            metrics["raw_callbacks_received"] = metrics["transport_messages_enqueued"]
        if "transport_messages_dequeued" in metrics:
            metrics["raw_callbacks_dequeued"] = metrics["transport_messages_dequeued"]
        if "transport_current_depth" in metrics:
            metrics["ingestion_queue_depth"] = metrics["transport_current_depth"]
        if "transport_high_water_depth" in metrics:
            metrics["ingestion_high_water"] = metrics["transport_high_water_depth"]
        if self._authoritative_lane is not None:
            metrics.update(self._authoritative_lane.metrics())
        return metrics

    def close(self) -> None:
        self.stop()
        self.disconnect()

    def _complete_reference_warmup(
        self,
        asset_classes: tuple[AssetClass, ...],
        force_reference_refresh: bool,
    ) -> None:
        if self._reference_stop.is_set():
            return
        try:
            getattr(self._engine, "refresh_universe")(
                asset_classes,
                force_reference_refresh=force_reference_refresh,
            )
            if not callable(getattr(self._engine, "set_reference_ready_observer", None)):
                self._notify_readiness()
        except Exception:
            # The runtime consumer remains alive; the existing scanner
            # qualification failure path owns reporting of warmup errors.
            return

    def _start_universe_refresh(
        self, asset_classes: tuple[AssetClass, ...],
    ) -> None:
        """Refresh discovery off the market-event callback path.

        The provider is intentionally polled from a bounded daemon worker so
        newly active symbols can enter a running scanner without requiring a
        restart.  A non-positive interval is a supported opt-out for tests and
        integrations that own refresh scheduling themselves.
        """
        if self._universe_refresh_interval_seconds <= 0:
            return
        thread = self._universe_refresh_thread
        if thread is not None and thread.is_alive():
            return
        self._universe_refresh_asset_classes = tuple(asset_classes)
        self._universe_refresh_stop.clear()
        self._universe_refresh_thread = Thread(
            target=self._refresh_universe_loop,
            name="realtime-universe-refresh",
            daemon=True,
        )
        self._universe_refresh_thread.start()

    def _stop_universe_refresh(self) -> None:
        self._universe_refresh_stop.set()
        thread = self._universe_refresh_thread
        if thread is not None:
            thread.join(2.0)
            self._universe_refresh_thread = None

    def _refresh_universe_loop(self) -> None:
        while not self._universe_refresh_stop.wait(
            self._universe_refresh_interval_seconds
        ):
            if not self._running:
                continue
            try:
                self.refresh_universe(self._universe_refresh_asset_classes)
            except Exception:
                # Discovery refresh is best effort; the active stream and its
                # last known universe remain authoritative until the next tick.
                continue

    def _notify_readiness(self) -> None:
        observer = self._readiness_observer
        if observer is not None and not self._reference_stop.is_set():
            try:
                observer()
            except Exception:
                # A non-authoritative readiness publisher must not stop
                # reference acquisition for the remaining symbols.
                return

    def set_event_observer(
        self,
        observer: Callable[[Any], object] | None,
    ) -> None:
        if observer is not None and not callable(observer):
            raise TypeError("event observer must be callable or None")
        self._event_observer = observer
        if (
            self._asynchronous_authoritative
            and observer is not None
            and self._authoritative_lane is None
        ):
            self._authoritative_lane = self._new_authoritative_lane()

    def _new_authoritative_lane(self) -> AuthoritativeEventLane:
        return AuthoritativeEventLane(
            self._dispatch_authoritative,
            capacity=self._authoritative_lane_capacity,
        )

    def set_retained_channels_source(
        self, source: Callable[[], Iterable[str]] | None,
    ) -> None:
        if source is not None and not callable(source):
            raise TypeError("retained channels source must be callable or None")
        self._retained_channels_source = source

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
    def last_failure_stage(self) -> str:
        return self._last_failure_stage

    @property
    def running(self) -> bool:
        return self._running

    @property
    def channels(self) -> tuple[str, ...]:
        return self._channels

    @property
    def active_symbols(self) -> tuple[str, ...]:
        return tuple(getattr(self._engine, "active_symbols", ()))

    @property
    def pending_reference_symbols(self) -> tuple[str, ...]:
        return tuple(getattr(self._engine, "pending_reference_symbols", ()))

    @property
    def qualification_ready(self) -> bool:
        return bool(self.active_symbols)

    @property
    def observation_ready(self) -> bool:
        return bool(self._channels)

    @property
    def retained_channels(self) -> tuple[str, ...]:
        return self._retained_channels()

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

    def _retained_channels(self) -> tuple[str, ...]:
        if self._retained_channels_source is None:
            return ()
        return _normalize_channels(self._retained_channels_source())

    def _effective_channels(
        self,
        scanner_channels: Iterable[str],
    ) -> tuple[str, ...]:
        # Webull permits at most 100 concurrently subscribed tickers.  Position
        # management channels are risk-critical, so reserve their capacity
        # first and fill the remaining bounded set from the scanner universe.
        retained = _normalize_channels_case_insensitive(
            self._retained_channels()
        )
        if len(retained) > self._maximum_subscription_channels:
            raise RuntimeError(
                "retained position channels exceed the market-data "
                "subscription limit"
            )
        retained_keys = {channel.casefold() for channel in retained}
        scanner = tuple(
            channel
            for channel in _normalize_channels_case_insensitive(scanner_channels)
            if channel.casefold() not in retained_keys
        )
        available = self._maximum_subscription_channels - len(retained)
        return tuple(sorted((*retained, *scanner[:available])))

    def _subscribe_effective(
        self,
        scanner_channels: Iterable[str],
    ) -> tuple[str, ...]:
        selected = self._effective_channels(scanner_channels)
        if not selected:
            raise ValueError("at least one channel is required")
        self._transport.subscribe(selected)
        self._channels = selected
        return selected

    def _sync_subscription(self) -> tuple[str, ...]:
        selected = self._effective_channels(self._scanner_channels)
        if selected == self._channels:
            return selected
        # Discovery can expose successive one-symbol bootstrap results while
        # the stable bounded observation set is assembled. Retain a valid
        # active set through that transient phase instead of repeatedly
        # creating an unsubscribe-all gap.
        if (
            self._subscription_bootstrap_pending
            and self._channels
            and selected
            # Bootstrap size is a property of discovery input, not the
            # effective union, which may include required retained symbols.
            and len(_normalize_channels(self._scanner_channels)) <= 1
        ):
            return self._channels
        if len(selected) > 1:
            self._subscription_bootstrap_pending = False
        if not selected:
            self._transport.subscribe(())
            self._channels = ()
            return selected
        return self._subscribe_effective(self._scanner_channels)

    def ensure_retained_channels(self) -> tuple[str, ...]:
        """Keep open-position symbols subscribed across session changes.

        Scanner-universe eligibility is an entry concern.  Retained Warrior
        positions are a risk-management concern and must remain subscribed
        through regular-to-after-hours transitions even when the next
        screener refresh no longer includes them.
        """
        self._require_connected()
        self._sync_subscription()
        return self._channels

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
        self._last_failure_stage = "CANONICAL_REDUCTION"
        scanner_started_at = datetime.now(UTC)
        performance_diagnostics.begin_latency_trace(event, scanner_started_at)
        try:
            decision = self._engine.consume(event)
            performance_diagnostics.mark_latency_trace_timestamp(
                "scanner_ended_at", datetime.now(UTC)
            )
            if self._authoritative_lane is not None:
                self._last_failure_stage = "AUTHORITATIVE_ADMISSION"
                self._authoritative_lane.publish(event)
            elif self._event_observer is not None:
                self._last_failure_stage = "DOWNSTREAM_MARKET_EVENT"
                observer_started_at = datetime.now(UTC)
                performance_diagnostics.mark_latency_trace_timestamp(
                    "observer_started_at", observer_started_at
                )
                observer_started = perf_counter()
                try:
                    self._event_observer(event)
                finally:
                    observer_ended_at = datetime.now(UTC)
                    performance_diagnostics.mark_latency_trace_timestamp(
                        "warrior_observation_completed_at", observer_ended_at,
                    )
                    if getattr(event, "received_timestamp", None) is not None:
                        performance_diagnostics.record_component_duration(
                            "callback_to_warrior_observation",
                            max(0.0, (
                                observer_ended_at - event.received_timestamp
                            ).total_seconds() * 1000.0),
                        )
                    performance_diagnostics.record_component_duration(
                        "canonical_state_to_warrior_observation",
                        (observer_started_at - scanner_started_at).total_seconds()
                        * 1000.0,
                    )
                    performance_diagnostics.record_observer_duration(
                        (perf_counter() - observer_started) * 1000.0
                    )
                    performance_diagnostics.mark_latency_trace_timestamp(
                        "observer_ended_at", observer_ended_at
                    )
            return decision
        finally:
            performance_diagnostics.finish_latency_trace(datetime.now(UTC))

    def _dispatch_authoritative(self, event: Any) -> object:
        started_at = datetime.now(UTC)
        performance_diagnostics.mark_latency_trace_timestamp(
            "warrior_observation_started_at", started_at,
        )
        observer = self._event_observer
        if observer is None:
            return None
        result = observer(event)
        ended_at = datetime.now(UTC)
        received_at = getattr(event, "received_timestamp", None)
        if received_at is not None:
            performance_diagnostics.record_component_duration(
                "callback_to_warrior_observation",
                max(0.0, (ended_at - received_at).total_seconds() * 1000.0),
            )
        performance_diagnostics.mark_latency_trace_timestamp(
            "warrior_observation_completed_at", ended_at,
        )
        return result

    def _start_background_workers(self) -> None:
        lane = self._authoritative_lane
        if lane is not None:
            if lane.failed:
                raise RuntimeError("authoritative lane worker failed")
            if not lane.running:
                self._authoritative_lane = lane = self._new_authoritative_lane()
            lane.start()
        if not self._asynchronous_authoritative:
            return
        drain = getattr(self._engine, "drain_evaluations", None)
        if not callable(drain) or (
            self._evaluation_thread is not None
            and self._evaluation_thread.is_alive()
        ):
            return
        self._evaluation_stop.clear()
        self._evaluation_thread = Thread(
            target=self._evaluation_loop,
            name="atlas-observational-evaluations",
            daemon=True,
        )
        self._evaluation_thread.start()

    def _stop_background_workers(self, *, stop_authoritative: bool = True) -> None:
        self._evaluation_stop.set()
        thread = self._evaluation_thread
        if thread is not None:
            thread.join(5.0)
            if thread.is_alive():
                raise RuntimeError("observational evaluation shutdown timed out")
            self._evaluation_thread = None
        lane = self._authoritative_lane
        if stop_authoritative and lane is not None:
            lane.stop()

    def _evaluation_loop(self) -> None:
        while not self._evaluation_stop.wait(0.001):
            drain = getattr(self._engine, "drain_evaluations", None)
            if not callable(drain):
                return
            try:
                drain(maximum=16)
            except Exception:
                # Observational failure must not mutate authoritative state;
                # the next iteration may recover after transient pressure.
                continue


def _normalize_channels(
    channels: Iterable[str],
) -> tuple[str, ...]:
    normalized = {
        str(channel).strip()
        for channel in channels
        if str(channel).strip()
    }

    return tuple(sorted(normalized))


def _normalize_channels_case_insensitive(
    channels: Iterable[str],
) -> tuple[str, ...]:
    normalized: dict[str, str] = {}
    for channel in channels:
        value = str(channel).strip()
        if value:
            normalized.setdefault(value.casefold(), value)
    return tuple(sorted(normalized.values()))

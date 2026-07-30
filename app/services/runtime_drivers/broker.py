"""Broker lifecycle driver for a desktop runtime session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event

from app.broker_plugins import BrokerRuntime
from app.configuration import OperationalConfiguration
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeEventSink,
    RuntimeHealthUpdate,
)


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class DesktopBrokerRuntimeDriver:
    """Own one configured broker connection for a desktop runtime session."""

    def __init__(
        self,
        *,
        configuration: OperationalConfiguration,
        broker_runtime: BrokerRuntime,
        event_sink: RuntimeEventSink,
        clock: Clock = utc_now,
        source: str = "desktop-broker-runtime",
    ) -> None:
        if not isinstance(configuration, OperationalConfiguration):
            raise TypeError(
                "configuration must be OperationalConfiguration"
            )
        if not isinstance(broker_runtime, BrokerRuntime):
            raise TypeError("broker_runtime must be BrokerRuntime")
        if broker_runtime.execution is None:
            raise ValueError(
                "configured broker runtime has no execution service"
            )
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be non-empty text")

        self._configuration = configuration
        self._broker_runtime = broker_runtime
        self._broker = broker_runtime.execution
        self._event_sink = event_sink
        self._clock = clock
        self._source = source.strip()
        self._sequence = 0
        self._connected = False

    @property
    def environment(self) -> str:
        return self._configuration.environment.value

    @property
    def active_model(self) -> str:
        return f"{self._broker_runtime.provider} broker"

    @property
    def cycles_completed(self) -> int:
        return 0

    def run(
        self,
        *,
        stop_event: Event,
        cycle_sink: Callable[[int], None],
    ) -> None:
        if not isinstance(stop_event, Event):
            raise TypeError("stop_event must be a threading Event")
        if not callable(cycle_sink):
            raise TypeError("cycle_sink must be callable")

        self._publish(
            "BROKER_CONNECTING",
            "Connecting to the configured broker.",
            runtime_status="STARTING",
            broker_status="CONNECTING",
        )

        try:
            self._broker.connect()
            self._connected = True
        except Exception as exc:
            self._publish(
                "BROKER_AUTHENTICATION_FAILED",
                "Configured broker authentication failed.",
                runtime_status="FAILED",
                broker_status="DISCONNECTED",
                last_error=f"{type(exc).__name__}: {exc}",
            )
            raise

        self._publish(
            "BROKER_AUTHENTICATED",
            "Configured broker connected and authenticated.",
            runtime_status="RUNNING",
            broker_status="CONNECTED",
        )

        try:
            stop_event.wait()
        finally:
            self._disconnect()

    def _disconnect(self) -> None:
        if not self._connected:
            return

        try:
            self._broker.disconnect()
        except Exception as exc:
            self._publish(
                "BROKER_DISCONNECT_FAILED",
                "Configured broker disconnect failed.",
                runtime_status="FAILED",
                broker_status="ERROR",
                last_error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            self._connected = False

        self._publish(
            "BROKER_DISCONNECTED",
            "Configured broker disconnected.",
            runtime_status="STOPPED",
            broker_status="DISCONNECTED",
        )

    def _publish(
        self,
        event_type: str,
        message: str,
        *,
        runtime_status: str,
        broker_status: str,
        last_error: str | None = None,
    ) -> None:
        timestamp = self._clock()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("broker runtime clock must be timezone-aware")

        self._sequence += 1
        self._event_sink(
            PaperRuntimeEvent(
                sequence=self._sequence,
                timestamp=timestamp,
                event_type=event_type,
                message=message,
                cycle=0,
                source=self._source,
                health=RuntimeHealthUpdate(
                    runtime_status=runtime_status,
                    broker_status=broker_status,
                    last_error=last_error,
                ),
            )
        )


__all__ = ["DesktopBrokerRuntimeDriver", "utc_now"]

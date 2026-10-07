from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum
import logging
from threading import Event, RLock, Thread
from typing import Protocol

from app.performance_diagnostics import (
    PerformanceDiagnostics,
    ShutdownOrigin,
    ShutdownReason,
    performance_diagnostics,
)
from app.services.runtime_driver_validation import validate_runtime_driver
from app.services.runtime_diagnostics import (
    log_runtime_exception,
    safe_exception_message,
)
from app.operations_core import (
    OperationsBus,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
)


_LOGGER = logging.getLogger("atlas.runtime")


class RuntimeServiceStatus(StrEnum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"


class RuntimeDriver(Protocol):
    """
    Backend-neutral runtime execution contract.

    A driver owns one runtime session. RuntimeService owns its thread and
    lifecycle; the driver owns the actual work performed inside that thread.
    """

    @property
    def environment(self) -> str:
        ...

    @property
    def active_model(self) -> str:
        ...

    @property
    def cycles_completed(self) -> int:
        ...

    def run(
        self,
        *,
        stop_event: Event,
        cycle_sink: Callable[[int], None],
    ) -> None:
        ...


DriverFactory = Callable[[], RuntimeDriver]


class RuntimeService:
    """
    Thread-safe lifecycle facade for a desktop runtime.

    Presentation clients issue commands through this service. They never create
    threads, invoke runtime engines directly, or publish lifecycle events.
    """

    def __init__(
        self,
        bus: OperationsBus,
        driver_factory: DriverFactory,
        *,
        source: str = "runtime-service",
        diagnostics: PerformanceDiagnostics = performance_diagnostics,
    ) -> None:
        if not callable(driver_factory):
            raise TypeError("driver_factory must be callable")

        if not source.strip():
            raise ValueError("source must not be empty")

        self._bus = bus
        self._driver_factory = driver_factory
        self._source = source.strip()
        self._diagnostics = diagnostics

        self._lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._driver: RuntimeDriver | None = None
        self._status = RuntimeServiceStatus.STOPPED
        self._stop_reason = "Runtime stopped cleanly."
        self._runtime_session_id: str | None = None

    @property
    def status(self) -> RuntimeServiceStatus:
        with self._lock:
            return self._status

    @property
    def is_active(self) -> bool:
        return self.status is not RuntimeServiceStatus.STOPPED

    @property
    def cycles_completed(self) -> int:
        with self._lock:
            driver = self._driver
            return 0 if driver is None else driver.cycles_completed

    def start(self) -> bool:
        """
        Start a new runtime session.

        Returns False when a session is already active. This makes repeated GUI
        button activation harmless rather than starting duplicate runtimes.
        """

        with self._lock:
            if self._status is not RuntimeServiceStatus.STOPPED:
                return False

            driver = self._driver_factory()
            validate_runtime_driver(driver)

            self._driver = driver
            self._stop_event = Event()
            self._stop_reason = "Runtime stopped cleanly."
            self._status = RuntimeServiceStatus.STARTING
            self._runtime_session_id = self._safe_diagnostic_call(
                "begin_runtime_session"
            )

            thread = Thread(
                target=self._run_driver,
                name="desktop-runtime-service",
                daemon=False,
            )
            self._thread = thread

        thread.start()
        return True

    def stop(
        self,
        reason: str = "Operator requested shutdown.",
        *,
        origin: ShutdownOrigin | str = ShutdownOrigin.OPERATOR_STOP,
        shutdown_reason: ShutdownReason | str = ShutdownReason.OPERATOR_REQUESTED,
        initiating_component: str = "runtime_service",
        operator_initiated: bool = True,
        failure_present: bool = False,
        exception_class: str | None = None,
    ) -> bool:
        """
        Request cooperative runtime shutdown.

        Returns False when no runtime is active. Shutdown completion is observed
        through RuntimeStopped and ApplicationState rather than a GUI callback.
        """

        normalized_reason = reason.strip()

        if not normalized_reason:
            raise ValueError("stop reason must not be empty")

        with self._lock:
            if self._status is RuntimeServiceStatus.STOPPED:
                return False

            self._record_shutdown_request(
                origin=origin,
                reason=shutdown_reason,
                component=initiating_component,
                failure_present=failure_present,
                operator_initiated=operator_initiated,
                exception_class=exception_class,
            )

            if self._status is not RuntimeServiceStatus.STOPPING:
                self._status = RuntimeServiceStatus.STOPPING
                self._stop_reason = normalized_reason

                self._bus.publish(
                    RuntimeStopping(
                        source=self._source,
                        reason=normalized_reason,
                    )
                )
                self._safe_diagnostic_call(
                    "record_shutdown_stopping",
                    runtime_state=self._status.value,
                )

            self._stop_event.set()

        return True

    def wait(self, timeout_seconds: float | None = None) -> bool:
        """
        Wait for runtime termination.

        Returns True when no runtime is active or the thread stopped before the
        timeout. This method does not force or kill a running thread.
        """

        if timeout_seconds is not None and timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")

        with self._lock:
            thread = self._thread

        if thread is None:
            return True

        thread.join(timeout_seconds)
        return not thread.is_alive()

    def close(self, timeout_seconds: float = 5.0) -> bool:
        """Request shutdown and wait for cooperative termination."""

        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")

        self.stop(
            "Application shutdown requested.",
            origin=ShutdownOrigin.APPLICATION_QUIT,
            shutdown_reason=ShutdownReason.APPLICATION_EXIT,
            initiating_component="desktop_composition",
            operator_initiated=True,
        )
        return self.wait(timeout_seconds)

    def note_shutdown_request(
        self,
        *,
        origin: ShutdownOrigin | str,
        reason: ShutdownReason | str,
        initiating_component: str,
        operator_initiated: bool,
        failure_present: bool = False,
        exception_class: str | None = None,
    ) -> None:
        """Record an initiating caller without changing runtime state."""
        with self._lock:
            self._record_shutdown_request(
                origin=origin,
                reason=reason,
                component=initiating_component,
                failure_present=failure_present,
                operator_initiated=operator_initiated,
                exception_class=exception_class,
            )

    def _run_driver(self) -> None:
        with self._lock:
            driver = self._driver

        if driver is None:
            return

        failure_present = False
        try:
            self._bus.publish(
                RuntimeStarting(
                    source=self._source,
                    environment=driver.environment,
                )
            )

            self._bus.publish(
                RuntimeStarted(
                    source=self._source,
                    environment=driver.environment,
                    active_model=driver.active_model,
                )
            )

            with self._lock:
                if self._status is RuntimeServiceStatus.STARTING:
                    self._status = RuntimeServiceStatus.RUNNING

            driver.run(
                stop_event=self._stop_event,
                cycle_sink=self._publish_cycle,
            )

            if not self._stop_event.is_set():
                self._record_shutdown_request(
                    origin=ShutdownOrigin.BACKGROUND_TASK_FAILURE,
                    reason=ShutdownReason.BACKGROUND_TASK_TERMINATED,
                    component="runtime_driver",
                    failure_present=True,
                    operator_initiated=False,
                )
                self._safe_diagnostic_call(
                    "record_shutdown_stopping",
                    runtime_state=self._status.value,
                )

            with self._lock:
                reason = self._stop_reason

            self._bus.publish(
                RuntimeStopped(
                    source=self._source,
                    reason=reason,
                    cycles_completed=driver.cycles_completed,
                )
            )

        except Exception as exc:
            failure_present = True
            with self._lock:
                lifecycle_status = self._status.value
            self._record_shutdown_request(
                origin=ShutdownOrigin.BACKGROUND_TASK_FAILURE,
                reason=ShutdownReason.BACKGROUND_TASK_TERMINATED,
                component="runtime_driver",
                failure_present=True,
                operator_initiated=False,
                exception_class=type(exc).__name__,
            )
            self._safe_diagnostic_call(
                "record_shutdown_stopping",
                runtime_state=lifecycle_status,
            )
            shutdown_requested = self._stop_event.is_set()
            log_runtime_exception(
                _LOGGER,
                exc,
                event_type="runtime_lifecycle_exception",
                lifecycle_phase=(
                    f"driver.run/{lifecycle_status.lower()}"
                ),
                shutdown_requested=shutdown_requested,
            )
            self._bus.publish(
                RuntimeFailed(
                    source=self._source,
                    error_message=safe_exception_message(exc),
                )
            )

        finally:
            with self._lock:
                self._status = RuntimeServiceStatus.STOPPED
                self._thread = None
                self._driver = None
                self._stop_event.set()
            self._safe_diagnostic_call(
                "record_shutdown_stopped",
                cleanup_completed=True,
                failure_present=failure_present,
            )

    def _record_shutdown_request(
        self,
        *,
        origin: ShutdownOrigin | str,
        reason: ShutdownReason | str,
        component: str,
        failure_present: bool,
        operator_initiated: bool,
        exception_class: str | None = None,
    ) -> None:
        self._safe_diagnostic_call(
            "record_shutdown_request",
            origin=origin,
            reason=reason,
            component=component,
            stop_event_already_set=self._stop_event.is_set(),
            failure_present=failure_present,
            operator_initiated=operator_initiated,
            runtime_session_id=self._runtime_session_id,
            exception_class=exception_class,
            runtime_state=self._status.value,
        )

    def _safe_diagnostic_call(self, method_name: str, **values):
        try:
            callback = getattr(self._diagnostics, method_name, None)
            if callable(callback):
                return callback(**values)
        except Exception:
            pass
        return None

    def _publish_cycle(self, cycle_count: int) -> None:
        self._bus.publish(
            RuntimeCycleCompleted(
                source=self._source,
                cycle_count=cycle_count,
            )
        )



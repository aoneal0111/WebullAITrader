"""Fail-closed lifecycle ownership for future SEC acquisition activation.

This module authorizes construction only after the machine-wide lease is
owned.  It performs no SEC transport construction itself and is not consumed
by the production desktop in Phase 3C-D1.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
from threading import Event, Lock, Thread, current_thread
from typing import TypeVar

from app.configuration.models import TradingEnvironment

from .network_lease import LeaseOutcome


DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15.0
HEARTBEAT_THREAD_NAME = "sec-network-lease-heartbeat"


class SecAcquisitionEligibilityReason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    ACQUISITION_DISABLED = "ACQUISITION_DISABLED"
    INELIGIBLE_ENVIRONMENT = "INELIGIBLE_ENVIRONMENT"
    SEC_CONFIGURATION_DISABLED = "SEC_CONFIGURATION_DISABLED"
    LEGACY_MIGRATION_DISABLED = "LEGACY_MIGRATION_DISABLED"


@dataclass(frozen=True, slots=True)
class SecAcquisitionEligibility:
    eligible: bool
    reason: SecAcquisitionEligibilityReason


def evaluate_sec_acquisition_eligibility(configuration: object) -> SecAcquisitionEligibility:
    """Evaluate the exact PAPER-only policy without acquiring a lease."""

    sec = getattr(configuration, "symbol_intelligence_sec_edgar", configuration)
    if not bool(getattr(sec, "acquisition_enabled", False)):
        return SecAcquisitionEligibility(
            False, SecAcquisitionEligibilityReason.ACQUISITION_DISABLED,
        )
    environment = getattr(configuration, "environment", None)
    try:
        normalized_environment = (
            environment
            if isinstance(environment, TradingEnvironment)
            else TradingEnvironment(str(getattr(environment, "value", environment)).upper())
        )
    except (TypeError, ValueError):
        return SecAcquisitionEligibility(
            False, SecAcquisitionEligibilityReason.INELIGIBLE_ENVIRONMENT,
        )
    if normalized_environment is not TradingEnvironment.PAPER:
        return SecAcquisitionEligibility(
            False, SecAcquisitionEligibilityReason.INELIGIBLE_ENVIRONMENT,
        )
    if not bool(getattr(sec, "enabled", False)):
        return SecAcquisitionEligibility(
            False, SecAcquisitionEligibilityReason.SEC_CONFIGURATION_DISABLED,
        )
    if (
        getattr(configuration, "sec_edgar", None) is not None
        and not bool(getattr(sec, "dual_network_migration_enabled", False))
    ):
        return SecAcquisitionEligibility(
            False, SecAcquisitionEligibilityReason.LEGACY_MIGRATION_DISABLED,
        )
    return SecAcquisitionEligibility(True, SecAcquisitionEligibilityReason.ELIGIBLE)


class SecNetworkOwnershipState(StrEnum):
    IDLE = "IDLE"
    ACQUIRING = "ACQUIRING"
    OWNED = "OWNED"
    LEASE_DENIED = "LEASE_DENIED"
    LEASE_ERROR = "LEASE_ERROR"
    OWNERSHIP_LOST = "OWNERSHIP_LOST"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    SHUTDOWN_INCOMPLETE = "SHUTDOWN_INCOMPLETE"
    CONSTRUCTION_ERROR = "CONSTRUCTION_ERROR"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class SecNetworkOwnershipDiagnostics:
    state: SecNetworkOwnershipState
    lease_outcome: LeaseOutcome | None
    lease_name: str
    owner_reference: str
    pid: int
    heartbeat_count: int
    heartbeat_failures: int
    last_heartbeat: datetime | None
    expires_at: datetime | None
    ownership_lost: bool
    admission_open: bool
    shutdown_incomplete: bool
    callback_failures: int
    release_failures: int
    last_error_category: str | None
    heartbeat_thread_alive: bool


T = TypeVar("T")


class SecNetworkOwnershipRuntime:
    """Own one lease and supervise it for a future acquisition lifecycle."""

    def __init__(
        self,
        lease: object,
        *,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        on_lease_lost: Callable[[], None] | None = None,
        heartbeat_wait: Callable[[Event, float], bool] | None = None,
        heartbeat_thread_factory: Callable[..., Thread] = Thread,
    ) -> None:
        ttl_seconds = float(getattr(lease, "ttl_seconds"))
        interval = float(heartbeat_interval_seconds)
        if interval <= 0.0 or interval > ttl_seconds / 3.0:
            raise ValueError("heartbeat interval must be positive and no more than one third of lease TTL")
        self._lease = lease
        self._interval = interval
        self._on_lease_lost = on_lease_lost or (lambda: None)
        self._wait = heartbeat_wait or (lambda event, timeout: event.wait(timeout))
        self._thread_factory = heartbeat_thread_factory
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._state = SecNetworkOwnershipState.IDLE
        self._lease_outcome: LeaseOutcome | None = None
        self._owns_lease = False
        self._admission_open = False
        self._ownership_lost = False
        self._shutdown_incomplete = False
        self._loss_callback_invoked = False
        self._heartbeat_count = 0
        self._heartbeat_failures = 0
        self._callback_failures = 0
        self._release_failures = 0
        self._last_error_category: str | None = None
        self._release_attempted = False
        self._admission_close_callback: Callable[[], None] = lambda: None
        self._worker_stop_callback: Callable[[float], bool] = lambda timeout: True
        self._resource_close_callback: Callable[[], None] = lambda: None
        self._admission_close_invoked = False
        self._resource_close_invoked = False

    @property
    def admission_open(self) -> bool:
        with self._lock:
            return self._admission_open and self._state is SecNetworkOwnershipState.OWNED

    @property
    def diagnostics(self) -> SecNetworkOwnershipDiagnostics:
        lease_diagnostics = self._lease.diagnostics()
        with self._lock:
            thread = self._thread
            return SecNetworkOwnershipDiagnostics(
                state=self._state,
                lease_outcome=self._lease_outcome,
                lease_name=str(getattr(lease_diagnostics, "lease_name", ""))[:128],
                owner_reference=_owner_reference(str(getattr(lease_diagnostics, "owner_id", ""))),
                pid=int(getattr(self._lease, "pid", 0)),
                heartbeat_count=self._heartbeat_count,
                heartbeat_failures=self._heartbeat_failures,
                last_heartbeat=getattr(lease_diagnostics, "last_heartbeat", None),
                expires_at=getattr(lease_diagnostics, "expires_at", None),
                ownership_lost=self._ownership_lost,
                admission_open=self._admission_open and self._state is SecNetworkOwnershipState.OWNED,
                shutdown_incomplete=self._shutdown_incomplete,
                callback_failures=self._callback_failures,
                release_failures=self._release_failures,
                last_error_category=self._last_error_category,
                heartbeat_thread_alive=bool(thread and thread.is_alive()),
            )

    def configure_shutdown(
        self,
        *,
        close_admission: Callable[[], None],
        stop_worker: Callable[[float], bool],
        close_resource: Callable[[], None],
    ) -> None:
        with self._lock:
            if self._state not in {SecNetworkOwnershipState.IDLE, SecNetworkOwnershipState.OWNED}:
                raise RuntimeError("shutdown callbacks cannot be changed in the current state")
            self._admission_close_callback = close_admission
            self._worker_stop_callback = stop_worker
            self._resource_close_callback = close_resource

    def authorize_construction(self, factory: Callable[[], T]) -> T | None:
        """Acquire, begin supervision, then authorize downstream construction."""

        with self._lock:
            if self._state is not SecNetworkOwnershipState.IDLE:
                raise RuntimeError("ownership runtime may authorize construction only once")
            self._state = SecNetworkOwnershipState.ACQUIRING
        result = self._lease.try_acquire()
        with self._lock:
            self._lease_outcome = result.outcome
            valid_outcome = result.outcome in {
                LeaseOutcome.ACQUIRED,
                LeaseOutcome.RENEWED,
                LeaseOutcome.TAKEN_OVER_EXPIRED,
            }
            if not result.is_owner or not valid_outcome:
                self._state = (
                    SecNetworkOwnershipState.LEASE_DENIED
                    if result.outcome is LeaseOutcome.DENIED
                    else SecNetworkOwnershipState.LEASE_ERROR
                )
                self._last_error_category = result.outcome.value
                return None
            self._owns_lease = True
            self._admission_open = True
            self._state = SecNetworkOwnershipState.OWNED
        self._start_heartbeat()
        try:
            value = factory()
            if not self.admission_open:
                raise RuntimeError("SEC network ownership was lost during construction")
            return value
        except Exception:
            self._cleanup_failed_construction()
            raise

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            if self._state is SecNetworkOwnershipState.CLOSED:
                return True
            if self._state in {
                SecNetworkOwnershipState.IDLE,
                SecNetworkOwnershipState.LEASE_DENIED,
                SecNetworkOwnershipState.LEASE_ERROR,
                SecNetworkOwnershipState.CONSTRUCTION_ERROR,
            } and not self._owns_lease:
                self._state = SecNetworkOwnershipState.CLOSED
                return True
            self._state = SecNetworkOwnershipState.SHUTTING_DOWN
            self._admission_open = False
        self._close_admission_once()
        try:
            worker_stopped = bool(self._worker_stop_callback(max(0.0, timeout_seconds)))
        except Exception:
            worker_stopped = False
            self._record_error("WORKER_STOP_CALLBACK_ERROR")
        if not worker_stopped:
            with self._lock:
                self._state = SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
                self._shutdown_incomplete = True
            return False
        self._close_resource_once()
        if not self._stop_heartbeat(timeout_seconds):
            with self._lock:
                self._state = SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
                self._shutdown_incomplete = True
                self._last_error_category = "HEARTBEAT_JOIN_TIMEOUT"
            return False
        with self._lock:
            should_release = self._owns_lease and not self._ownership_lost
            already_attempted = self._release_attempted
            if should_release and not already_attempted:
                self._release_attempted = True
        if should_release:
            if already_attempted:
                with self._lock:
                    self._state = SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
                    self._shutdown_incomplete = True
                return False
            if not self._lease.release():
                with self._lock:
                    self._release_failures += 1
                    self._state = SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
                    self._shutdown_incomplete = True
                    self._last_error_category = "LEASE_RELEASE_FAILED"
                return False
        with self._lock:
            self._owns_lease = False
            self._shutdown_incomplete = False
            self._state = SecNetworkOwnershipState.CLOSED
        return True

    def _start_heartbeat(self) -> None:
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("heartbeat supervisor already started")
            self._stop.clear()
            self._thread = self._thread_factory(
                target=self._heartbeat_loop,
                name=HEARTBEAT_THREAD_NAME,
                daemon=False,
            )
            thread = self._thread
        thread.start()

    def _heartbeat_loop(self) -> None:
        try:
            while not self._wait(self._stop, self._interval):
                try:
                    result = self._lease.heartbeat()
                except Exception:
                    self._lose_ownership("HEARTBEAT_EXCEPTION")
                    return
                if (
                    result.outcome is not LeaseOutcome.RENEWED
                    or not bool(result.is_owner)
                ):
                    self._lose_ownership(
                        "HEARTBEAT_" + str(result.outcome.value),
                        outcome=result.outcome,
                    )
                    return
                with self._lock:
                    self._lease_outcome = result.outcome
                    self._heartbeat_count += 1
        except Exception:
            self._lose_ownership("HEARTBEAT_SUPERVISOR_ERROR")

    def _lose_ownership(
        self,
        category: str,
        *,
        outcome: LeaseOutcome | None = None,
    ) -> None:
        with self._lock:
            if self._ownership_lost:
                return
            self._ownership_lost = True
            self._owns_lease = False
            self._admission_open = False
            self._heartbeat_failures += 1
            self._last_error_category = category[:64]
            if outcome is not None:
                self._lease_outcome = outcome
            self._state = SecNetworkOwnershipState.OWNERSHIP_LOST
            invoke = not self._loss_callback_invoked
            self._loss_callback_invoked = True
        if invoke:
            try:
                self._on_lease_lost()
            except Exception:
                with self._lock:
                    self._callback_failures += 1
                    self._last_error_category = "LEASE_LOSS_CALLBACK_ERROR"

    def _cleanup_failed_construction(self) -> None:
        heartbeat_stopped = self._stop_heartbeat(5.0)
        with self._lock:
            self._admission_open = False
            if not heartbeat_stopped:
                self._state = SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
                self._shutdown_incomplete = True
                self._last_error_category = "HEARTBEAT_JOIN_TIMEOUT"
                return
            should_release = self._owns_lease and not self._ownership_lost
            self._release_attempted = should_release
        released = True
        if should_release:
            released = bool(self._lease.release())
        with self._lock:
            if should_release and not released:
                self._release_failures += 1
                self._last_error_category = "LEASE_RELEASE_FAILED"
            self._owns_lease = False
            self._state = SecNetworkOwnershipState.CONSTRUCTION_ERROR

    def _close_admission_once(self) -> None:
        with self._lock:
            if self._admission_close_invoked:
                return
            self._admission_close_invoked = True
        try:
            self._admission_close_callback()
        except Exception:
            self._record_error("ADMISSION_CLOSE_CALLBACK_ERROR")

    def _close_resource_once(self) -> None:
        with self._lock:
            if self._resource_close_invoked:
                return
            self._resource_close_invoked = True
        try:
            self._resource_close_callback()
        except Exception:
            self._record_error("RESOURCE_CLOSE_CALLBACK_ERROR")

    def _stop_heartbeat(self, timeout_seconds: float) -> bool:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(max(0.0, timeout_seconds))
        return not bool(thread and thread.is_alive())

    def _record_error(self, category: str) -> None:
        with self._lock:
            self._last_error_category = category[:64]


def _owner_reference(owner_id: str) -> str:
    return "owner-" + hashlib.sha256(owner_id.encode("utf-8", "replace")).hexdigest()[:12]


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "HEARTBEAT_THREAD_NAME",
    "SecAcquisitionEligibility",
    "SecAcquisitionEligibilityReason",
    "SecNetworkOwnershipDiagnostics",
    "SecNetworkOwnershipRuntime",
    "SecNetworkOwnershipState",
    "evaluate_sec_acquisition_eligibility",
]

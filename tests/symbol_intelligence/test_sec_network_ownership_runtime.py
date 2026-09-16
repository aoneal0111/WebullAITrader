from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace

import pytest

from app.configuration.models import TradingEnvironment
from app.symbol_intelligence.network_lease import (
    LEASE_NAME,
    LeaseOutcome,
    LeaseResult,
    SecNetworkOwnershipLease,
)
from app.symbol_intelligence.network_ownership_runtime import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_THREAD_NAME,
    SecAcquisitionEligibilityReason,
    SecNetworkOwnershipRuntime,
    SecNetworkOwnershipState,
    evaluate_sec_acquisition_eligibility,
)


T0 = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)


class Clock:
    def __init__(self, value: datetime = T0) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class ManualWait:
    def __init__(self) -> None:
        self.pulse_event = Event()

    def __call__(self, stop: Event, interval: float) -> bool:
        while not stop.is_set():
            if self.pulse_event.wait(0.01):
                self.pulse_event.clear()
                return stop.is_set()
        return True

    def pulse(self) -> None:
        self.pulse_event.set()


class ControlledHeartbeatThread:
    def __init__(
        self,
        *,
        target,
        name: str,
        daemon: bool,
        join_succeeds: bool,
        events: list[str] | None = None,
    ) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.join_succeeds = join_succeeds
        self.events = events
        self.alive = False

    def start(self) -> None:
        self.alive = True

    def join(self, timeout: float) -> None:
        if self.join_succeeds:
            self.alive = False
            if self.events is not None:
                self.events.append("HEARTBEAT_JOIN_SUCCESS")
        elif self.events is not None:
            self.events.append("HEARTBEAT_JOIN_TIMEOUT")

    def is_alive(self) -> bool:
        return self.alive


class FakeLease:
    ttl_seconds = 45.0
    lease_name = LEASE_NAME
    owner_id = "full-owner-uuid-must-not-escape"
    pid = 321

    def __init__(
        self,
        acquire: LeaseResult = LeaseResult(
            LeaseOutcome.ACQUIRED, True, T0 + timedelta(seconds=45),
        ),
        heartbeats: list[LeaseResult] | None = None,
    ) -> None:
        self.acquire_result = acquire
        self.heartbeats = list(heartbeats or [])
        self.acquire_count = 0
        self.heartbeat_count = 0
        self.release_count = 0
        self.owned = False
        self.last_heartbeat = T0
        self.expires_at = acquire.expires_at
        self.heartbeat_seen = Event()

    def try_acquire(self) -> LeaseResult:
        self.acquire_count += 1
        self.owned = self.acquire_result.is_owner
        return self.acquire_result

    def heartbeat(self) -> LeaseResult:
        self.heartbeat_count += 1
        result = (
            self.heartbeats.pop(0)
            if self.heartbeats
            else LeaseResult(
                LeaseOutcome.RENEWED,
                True,
                T0 + timedelta(seconds=45 + self.heartbeat_count),
            )
        )
        self.owned = result.is_owner
        self.last_heartbeat = T0 + timedelta(seconds=self.heartbeat_count)
        self.expires_at = result.expires_at
        self.heartbeat_seen.set()
        return result

    def release(self) -> bool:
        self.release_count += 1
        if not self.owned:
            return False
        self.owned = False
        return True

    def diagnostics(self):
        return SimpleNamespace(
            lease_name=self.lease_name,
            owner_id=self.owner_id,
            is_owner=self.owned,
            expires_at=self.expires_at,
            last_heartbeat=self.last_heartbeat,
        )


def config(
    *,
    environment: TradingEnvironment = TradingEnvironment.PAPER,
    acquisition: bool = True,
    sec_enabled: bool = True,
    migration: bool = False,
    legacy: bool = False,
):
    return SimpleNamespace(
        environment=environment,
        sec_edgar=object() if legacy else None,
        symbol_intelligence_sec_edgar=SimpleNamespace(
            acquisition_enabled=acquisition,
            enabled=sec_enabled,
            dual_network_migration_enabled=migration,
        ),
    )


@pytest.mark.parametrize("environment", list(TradingEnvironment))
def test_exact_environment_matrix_is_paper_only(environment) -> None:
    result = evaluate_sec_acquisition_eligibility(config(environment=environment))
    assert result.eligible is (environment is TradingEnvironment.PAPER)
    assert result.reason is (
        SecAcquisitionEligibilityReason.ELIGIBLE
        if environment is TradingEnvironment.PAPER
        else SecAcquisitionEligibilityReason.INELIGIBLE_ENVIRONMENT
    )


@pytest.mark.parametrize(
    ("acquisition", "sec_enabled", "legacy", "migration", "eligible", "reason"),
    (
        (False, True, False, False, False, SecAcquisitionEligibilityReason.ACQUISITION_DISABLED),
        (False, True, True, True, False, SecAcquisitionEligibilityReason.ACQUISITION_DISABLED),
        (True, False, False, False, False, SecAcquisitionEligibilityReason.SEC_CONFIGURATION_DISABLED),
        (True, True, False, False, True, SecAcquisitionEligibilityReason.ELIGIBLE),
        (True, True, False, True, True, SecAcquisitionEligibilityReason.ELIGIBLE),
        (True, True, True, False, False, SecAcquisitionEligibilityReason.LEGACY_MIGRATION_DISABLED),
        (True, True, True, True, True, SecAcquisitionEligibilityReason.ELIGIBLE),
    ),
)
def test_legacy_migration_matrix(acquisition, sec_enabled, legacy, migration, eligible, reason) -> None:
    result = evaluate_sec_acquisition_eligibility(config(
        acquisition=acquisition,
        sec_enabled=sec_enabled,
        legacy=legacy,
        migration=migration,
    ))
    assert result.eligible is eligible
    assert result.reason is reason


def test_non_paper_migration_never_overrides_environment_gate() -> None:
    for environment in TradingEnvironment:
        if environment is TradingEnvironment.PAPER:
            continue
        result = evaluate_sec_acquisition_eligibility(config(
            environment=environment, legacy=True, migration=True,
        ))
        assert result.eligible is False
        assert result.reason is SecAcquisitionEligibilityReason.INELIGIBLE_ENVIRONMENT


def test_denial_and_error_precede_factory_and_do_not_start_heartbeat() -> None:
    for outcome in (LeaseOutcome.DENIED, LeaseOutcome.ERROR):
        lease = FakeLease(LeaseResult(outcome, False, T0 + timedelta(seconds=45)))
        called = []
        runtime = SecNetworkOwnershipRuntime(lease)
        assert runtime.authorize_construction(lambda: called.append("factory")) is None
        assert called == []
        assert lease.heartbeat_count == 0
        assert runtime.diagnostics.state is (
            SecNetworkOwnershipState.LEASE_DENIED
            if outcome is LeaseOutcome.DENIED
            else SecNetworkOwnershipState.LEASE_ERROR
        )


@pytest.mark.parametrize(
    "outcome",
    (LeaseOutcome.ACQUIRED, LeaseOutcome.RENEWED, LeaseOutcome.TAKEN_OVER_EXPIRED),
)
def test_owner_outcomes_authorize_only_after_acquire_and_heartbeat_start(outcome) -> None:
    events = []

    class OrderedLease(FakeLease):
        def try_acquire(self):
            events.append("acquire")
            return super().try_acquire()

    lease = OrderedLease(LeaseResult(outcome, True, T0 + timedelta(seconds=45)))
    runtime = SecNetworkOwnershipRuntime(lease)

    def factory():
        events.append("factory")
        assert runtime.diagnostics.heartbeat_thread_alive is True
        return object()

    assert runtime.authorize_construction(factory) is not None
    assert events == ["acquire", "factory"]
    assert lease.acquire_count == 1
    assert runtime.close()


def test_construction_failure_stops_heartbeat_and_releases_original_error() -> None:
    lease = FakeLease()
    runtime = SecNetworkOwnershipRuntime(lease)

    def fail():
        raise ValueError("offline construction failure")

    with pytest.raises(ValueError, match="offline construction failure"):
        runtime.authorize_construction(fail)
    diagnostics = runtime.diagnostics
    assert diagnostics.state is SecNetworkOwnershipState.CONSTRUCTION_ERROR
    assert diagnostics.heartbeat_thread_alive is False
    assert lease.release_count == 1
    assert lease.owned is False


def test_construction_failure_never_releases_with_live_heartbeat() -> None:
    lease = FakeLease()
    thread = None

    def thread_factory(**kwargs):
        nonlocal thread
        thread = ControlledHeartbeatThread(**kwargs, join_succeeds=False)
        return thread

    runtime = SecNetworkOwnershipRuntime(lease, heartbeat_thread_factory=thread_factory)
    with pytest.raises(ValueError, match="primary construction failure"):
        runtime.authorize_construction(
            lambda: (_ for _ in ()).throw(ValueError("primary construction failure"))
        )

    diagnostics = runtime.diagnostics
    assert thread is not None and thread.is_alive()
    assert diagnostics.state is SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
    assert diagnostics.shutdown_incomplete is True
    assert diagnostics.heartbeat_thread_alive is True
    assert diagnostics.last_error_category == "HEARTBEAT_JOIN_TIMEOUT"
    assert lease.release_count == 0
    assert lease.owned is True


def test_incomplete_construction_cleanup_retries_after_heartbeat_terminates() -> None:
    lease = FakeLease()
    thread = None

    def thread_factory(**kwargs):
        nonlocal thread
        thread = ControlledHeartbeatThread(**kwargs, join_succeeds=False)
        return thread

    runtime = SecNetworkOwnershipRuntime(lease, heartbeat_thread_factory=thread_factory)
    with pytest.raises(RuntimeError, match="construction failed"):
        runtime.authorize_construction(
            lambda: (_ for _ in ()).throw(RuntimeError("construction failed"))
        )
    assert thread is not None
    assert lease.release_count == 0
    assert runtime.diagnostics.state is SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE

    thread.join_succeeds = True
    assert runtime.close(timeout_seconds=0.1) is True
    assert lease.release_count == 1
    assert lease.owned is False
    assert runtime.diagnostics.state is SecNetworkOwnershipState.CLOSED
    assert runtime.close(timeout_seconds=0.1) is True
    assert lease.release_count == 1


def test_repeated_heartbeat_renews_without_reacquire_or_duplicate_supervisor() -> None:
    waiter = ManualWait()
    lease = FakeLease()
    runtime = SecNetworkOwnershipRuntime(
        lease, heartbeat_wait=waiter, heartbeat_interval_seconds=15.0,
    )
    assert runtime.authorize_construction(object) is not None
    thread = runtime._thread
    for expected in (1, 2):
        lease.heartbeat_seen.clear()
        waiter.pulse()
        assert lease.heartbeat_seen.wait(1.0)
        deadline = time.monotonic() + 1.0
        while runtime.diagnostics.heartbeat_count < expected and time.monotonic() < deadline:
            time.sleep(0.001)
        assert runtime.diagnostics.heartbeat_count == expected
    assert runtime._thread is thread
    assert thread is not None and thread.name == HEARTBEAT_THREAD_NAME
    assert lease.acquire_count == 1
    assert runtime.close()


def test_heartbeat_loss_closes_admission_and_calls_callback_once() -> None:
    waiter = ManualWait()
    lease = FakeLease(heartbeats=[
        LeaseResult(LeaseOutcome.DENIED, False, T0 + timedelta(seconds=45)),
    ])
    callbacks = []
    runtime = SecNetworkOwnershipRuntime(
        lease, heartbeat_wait=waiter, on_lease_lost=lambda: callbacks.append("lost"),
    )
    assert runtime.authorize_construction(object) is not None
    waiter.pulse()
    assert lease.heartbeat_seen.wait(1.0)
    deadline = time.monotonic() + 1.0
    while not runtime.diagnostics.ownership_lost and time.monotonic() < deadline:
        time.sleep(0.001)
    assert runtime.admission_open is False
    assert runtime.diagnostics.state is SecNetworkOwnershipState.OWNERSHIP_LOST
    assert callbacks == ["lost"]
    assert runtime.close()
    assert lease.release_count == 0


def test_lease_loss_callback_failure_is_contained_and_redacted() -> None:
    waiter = ManualWait()
    lease = FakeLease(heartbeats=[LeaseResult(LeaseOutcome.ERROR, False)])

    def fail_callback():
        raise RuntimeError("contact@example.invalid must not escape")

    runtime = SecNetworkOwnershipRuntime(
        lease, heartbeat_wait=waiter, on_lease_lost=fail_callback,
    )
    assert runtime.authorize_construction(object) is not None
    waiter.pulse()
    assert lease.heartbeat_seen.wait(1.0)
    deadline = time.monotonic() + 1.0
    while runtime.diagnostics.callback_failures == 0 and time.monotonic() < deadline:
        time.sleep(0.001)
    diagnostics = runtime.diagnostics
    assert diagnostics.ownership_lost is True
    assert diagnostics.callback_failures == 1
    assert diagnostics.last_error_category == "LEASE_LOSS_CALLBACK_ERROR"
    assert lease.owner_id not in repr(diagnostics)
    assert "contact@example.invalid" not in repr(diagnostics)
    assert runtime.close()


def test_shutdown_timeout_retains_lease_and_second_attempt_completes_in_order() -> None:
    events = []

    class OrderedLease(FakeLease):
        def release(self) -> bool:
            events.append("LEASE_RELEASE")
            return super().release()

    lease = OrderedLease()
    worker_dead = False
    heartbeat_thread = None

    def thread_factory(**kwargs):
        nonlocal heartbeat_thread
        heartbeat_thread = ControlledHeartbeatThread(
            **kwargs, join_succeeds=True, events=events,
        )
        return heartbeat_thread

    runtime = SecNetworkOwnershipRuntime(
        lease, heartbeat_thread_factory=thread_factory,
    )
    assert runtime.authorize_construction(object) is not None

    def stop_worker(timeout: float) -> bool:
        events.append("WORKER_STOP_REQUEST")
        events.append("WORKER_JOIN_SUCCESS" if worker_dead else "WORKER_JOIN_TIMEOUT")
        return worker_dead

    runtime.configure_shutdown(
        close_admission=lambda: events.append("ADMISSION_CLOSE"),
        stop_worker=stop_worker,
        close_resource=lambda: events.append("RESOURCE_CLOSE"),
    )
    original_stop_heartbeat = runtime._stop_heartbeat

    def stop_heartbeat(timeout: float) -> bool:
        events.append("HEARTBEAT_STOP")
        return original_stop_heartbeat(timeout)

    runtime._stop_heartbeat = stop_heartbeat
    assert runtime.close(timeout_seconds=0.1) is False
    assert runtime.diagnostics.state is SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
    assert runtime.diagnostics.heartbeat_thread_alive is True
    assert lease.owned is True
    assert lease.release_count == 0
    assert events == [
        "ADMISSION_CLOSE",
        "WORKER_STOP_REQUEST",
        "WORKER_JOIN_TIMEOUT",
    ]

    worker_dead = True
    assert runtime.close(timeout_seconds=1.0) is True
    assert events == [
        "ADMISSION_CLOSE",
        "WORKER_STOP_REQUEST",
        "WORKER_JOIN_TIMEOUT",
        "WORKER_STOP_REQUEST",
        "WORKER_JOIN_SUCCESS",
        "RESOURCE_CLOSE",
        "HEARTBEAT_STOP",
        "HEARTBEAT_JOIN_SUCCESS",
        "LEASE_RELEASE",
    ]
    assert lease.release_count == 1
    assert runtime.diagnostics.state is SecNetworkOwnershipState.CLOSED
    assert runtime.close() is True
    assert lease.release_count == 1


def test_runtime_contention_denies_before_factory_then_allows_after_release(tmp_path: Path) -> None:
    path = tmp_path / "runtime-lease.sqlite3"
    clock = Clock()
    lease_a = SecNetworkOwnershipLease(path, owner_id="a", host_id="host", clock=clock)
    lease_b = SecNetworkOwnershipLease(path, owner_id="b", host_id="host", clock=clock)
    runtime_a = SecNetworkOwnershipRuntime(lease_a)
    runtime_b = SecNetworkOwnershipRuntime(lease_b)
    assert runtime_a.authorize_construction(object) is not None
    called = []
    assert runtime_b.authorize_construction(lambda: called.append("network")) is None
    assert called == []
    assert runtime_b.diagnostics.state is SecNetworkOwnershipState.LEASE_DENIED
    assert runtime_a.close()

    lease_c = SecNetworkOwnershipLease(path, owner_id="c", host_id="host", clock=clock)
    runtime_c = SecNetworkOwnershipRuntime(lease_c)
    assert runtime_c.authorize_construction(object) is not None
    assert runtime_c.close()


def test_expired_takeover_makes_stale_owner_fail_closed_without_releasing_successor(tmp_path: Path) -> None:
    path = tmp_path / "takeover.sqlite3"
    clock = Clock()
    waiter = ManualWait()
    lease_a = SecNetworkOwnershipLease(
        path, owner_id="a", host_id="host", clock=clock, ttl_seconds=10,
    )
    runtime_a = SecNetworkOwnershipRuntime(
        lease_a, heartbeat_interval_seconds=3, heartbeat_wait=waiter,
    )
    assert runtime_a.authorize_construction(object) is not None
    clock.value = T0 + timedelta(seconds=10)
    lease_b = SecNetworkOwnershipLease(
        path, owner_id="b", host_id="host", clock=clock, ttl_seconds=10,
    )
    runtime_b = SecNetworkOwnershipRuntime(lease_b, heartbeat_interval_seconds=3)
    assert runtime_b.authorize_construction(object) is not None
    assert runtime_b.diagnostics.lease_outcome is LeaseOutcome.TAKEN_OVER_EXPIRED

    waiter.pulse()
    deadline = time.monotonic() + 1.0
    while not runtime_a.diagnostics.ownership_lost and time.monotonic() < deadline:
        time.sleep(0.001)
    assert runtime_a.diagnostics.ownership_lost is True
    assert runtime_a.close()
    assert runtime_b.diagnostics.admission_open is True
    assert lease_a.release() is False
    assert runtime_b.close()


def test_default_interval_and_relative_validation() -> None:
    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS == 15.0
    with pytest.raises(ValueError, match="one third"):
        SecNetworkOwnershipRuntime(FakeLease(), heartbeat_interval_seconds=15.1)
    with pytest.raises(ValueError, match="positive"):
        SecNetworkOwnershipRuntime(FakeLease(), heartbeat_interval_seconds=0)


def test_release_failure_is_bounded_and_not_retried_on_repeated_close() -> None:
    class FailingReleaseLease(FakeLease):
        def release(self) -> bool:
            self.release_count += 1
            return False

    lease = FailingReleaseLease()
    runtime = SecNetworkOwnershipRuntime(lease)
    assert runtime.authorize_construction(object) is not None
    assert runtime.close() is False
    diagnostics = runtime.diagnostics
    assert diagnostics.state is SecNetworkOwnershipState.SHUTDOWN_INCOMPLETE
    assert diagnostics.release_failures == 1
    assert diagnostics.last_error_category == "LEASE_RELEASE_FAILED"
    assert runtime.close() is False
    assert lease.release_count == 1
    assert runtime.diagnostics.release_failures == 1

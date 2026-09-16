from __future__ import annotations

from datetime import UTC, datetime, timedelta
import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from app.symbol_intelligence.network_lease import (
    DEFAULT_TTL_SECONDS,
    LEASE_NAME,
    LeaseOutcome,
    LeaseSchemaError,
    SecNetworkOwnershipLease,
)


T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self, value: datetime = T0):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def make(path: Path, owner: str, clock: Clock, **kwargs) -> SecNetworkOwnershipLease:
    return SecNetworkOwnershipLease(path, owner_id=owner, host_id="host-test", pid=100, clock=clock, **kwargs)


def test_first_acquire_and_same_owner_renew(tmp_path: Path) -> None:
    clock = Clock()
    lease = make(tmp_path / "lease.sqlite3", "a", clock)
    first = lease.try_acquire()
    assert first.outcome is LeaseOutcome.ACQUIRED and first.is_owner
    acquired = first.expires_at
    clock.value = T0 + timedelta(seconds=2)
    renewed = lease.try_acquire()
    assert renewed.outcome is LeaseOutcome.RENEWED
    assert renewed.expires_at > acquired
    assert lease.metrics.renewals == 1


def test_second_owner_denied_and_expired_takeover(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "lease.sqlite3"
    owner_a = make(path, "a", clock, ttl_seconds=10)
    owner_b = make(path, "b", clock, ttl_seconds=10)
    assert owner_a.try_acquire().outcome is LeaseOutcome.ACQUIRED
    assert owner_b.try_acquire().outcome is LeaseOutcome.DENIED
    clock.value = T0 + timedelta(seconds=10)
    assert owner_b.try_acquire().outcome is LeaseOutcome.TAKEN_OVER_EXPIRED
    assert owner_a.heartbeat().outcome is LeaseOutcome.DENIED


def test_expired_owner_heartbeat_cannot_revive_before_reacquire(tmp_path: Path) -> None:
    clock = Clock()
    lease = make(tmp_path / "lease.sqlite3", "a", clock, ttl_seconds=10)
    acquired = lease.try_acquire()
    assert acquired.outcome is LeaseOutcome.ACQUIRED
    original_expiry = acquired.expires_at
    clock.value = T0 + timedelta(seconds=10)
    rejected = lease.heartbeat()
    assert rejected.outcome is LeaseOutcome.DENIED
    assert rejected.is_owner is False
    diagnostics = lease.diagnostics()
    assert diagnostics.is_owner is False
    assert diagnostics.last_heartbeat == T0
    assert diagnostics.expires_at == original_expiry
    assert lease.metrics.heartbeat_failures == 1
    reacquired = lease.try_acquire()
    assert reacquired.outcome is LeaseOutcome.TAKEN_OVER_EXPIRED
    assert reacquired.is_owner is True


def test_expiry_boundary_and_heartbeat(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "lease.sqlite3"
    lease = make(path, "a", clock, ttl_seconds=10)
    other = make(path, "b", clock, ttl_seconds=10)
    assert lease.try_acquire().outcome is LeaseOutcome.ACQUIRED
    clock.value = T0 + timedelta(seconds=9)
    assert lease.heartbeat().outcome is LeaseOutcome.RENEWED
    clock.value = T0 + timedelta(seconds=18)
    assert lease.heartbeat().outcome is LeaseOutcome.RENEWED
    clock.value = T0 + timedelta(seconds=19)
    assert lease.try_acquire().outcome is LeaseOutcome.RENEWED
    clock.value = T0 + timedelta(seconds=29)
    assert other.try_acquire().outcome is LeaseOutcome.TAKEN_OVER_EXPIRED


def test_release_is_owner_only_and_idempotent(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "lease.sqlite3"
    a, b = make(path, "a", clock), make(path, "b", clock)
    assert a.try_acquire().is_owner
    assert not b.release()
    assert a.release()
    assert not a.release()
    assert b.try_acquire().outcome is LeaseOutcome.ACQUIRED


def test_clean_release_and_crash_recovery(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "lease.sqlite3"
    a = make(path, "a", clock, ttl_seconds=10)
    b = make(path, "b", clock, ttl_seconds=10)
    a.try_acquire()
    clock.value = T0 + timedelta(seconds=11)
    assert b.try_acquire().outcome is LeaseOutcome.TAKEN_OVER_EXPIRED
    assert a.release() is False


def test_machine_wide_paper_live_compete_on_same_name(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "lease.sqlite3"
    paper = make(path, "paper", clock)
    live = make(path, "live", clock)
    assert paper.lease_name == live.lease_name == LEASE_NAME
    assert paper.try_acquire().is_owner
    assert not live.try_acquire().is_owner


def test_thread_concurrent_acquire_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "lease.sqlite3"
    barrier = threading.Barrier(8)

    def contender(index: int) -> LeaseOutcome:
        lease = make(path, str(index), Clock())
        barrier.wait()
        return lease.try_acquire().outcome

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(contender, range(8)))
    assert sum(outcome in {LeaseOutcome.ACQUIRED, LeaseOutcome.TAKEN_OVER_EXPIRED} for outcome in outcomes) == 1


def _process_contender(path: str, owner: str, gate, result_queue) -> None:
    lease = make(Path(path), owner, Clock())
    gate.wait()
    result_queue.put(lease.try_acquire().outcome.value)


def _process_acquire_without_release(path: str, owner: str, result_queue) -> None:
    lease = make(Path(path), owner, Clock(T0), ttl_seconds=10)
    result = lease.try_acquire()
    result_queue.put((result.outcome.value, result.expires_at.isoformat()))


def test_multi_process_contention(tmp_path: Path) -> None:
    path = tmp_path / "lease.sqlite3"
    context = multiprocessing.get_context("spawn")
    gate = context.Barrier(2)
    result_queue = context.Queue()
    processes = [context.Process(target=_process_contender, args=(str(path), str(i), gate, result_queue)) for i in range(2)]
    for process in processes:
        process.start()
    outcomes = [result_queue.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)
    assert sorted(outcomes) == [LeaseOutcome.ACQUIRED.value, LeaseOutcome.DENIED.value]
    assert all(process.exitcode == 0 for process in processes)


def test_multi_process_expiry_takeover_without_release(tmp_path: Path) -> None:
    path = tmp_path / "lease.sqlite3"
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    owner_a = context.Process(
        target=_process_acquire_without_release,
        args=(str(path), "process-a", result_queue),
    )
    owner_a.start()
    try:
        outcome, expiry = result_queue.get(timeout=10)
        owner_a.join(timeout=10)
        assert owner_a.exitcode == 0
    finally:
        if owner_a.is_alive():
            owner_a.terminate()
            owner_a.join(timeout=10)
    assert outcome == LeaseOutcome.ACQUIRED.value
    assert expiry.endswith("+00:00")

    owner_b = make(path, "process-b", Clock(T0 + timedelta(seconds=10)), ttl_seconds=10)
    takeover = owner_b.try_acquire()
    assert takeover.outcome is LeaseOutcome.TAKEN_OVER_EXPIRED
    assert takeover.is_owner
    assert owner_b.diagnostics().owner_id == "process-b"
    assert owner_b.diagnostics().is_owner


def test_concurrent_initialize_and_schema_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "lease.sqlite3"
    leases = [make(path, str(i), Clock()) for i in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda item: item.initialize(), leases))
    connection = sqlite3.connect(path)
    connection.execute("UPDATE lease_metadata SET value='99' WHERE key='schema_version'")
    connection.commit()
    connection.close()
    with pytest.raises(LeaseSchemaError):
        leases[0].initialize()


def test_backwards_clock_fails_closed_and_diagnostics_are_bounded(tmp_path: Path) -> None:
    clock = Clock()
    lease = make(tmp_path / "lease.sqlite3", "a", clock, ttl_seconds=10)
    lease.try_acquire()
    clock.value = T0 - timedelta(seconds=1)
    result = lease.heartbeat()
    assert result.outcome is LeaseOutcome.DENIED
    diagnostics = lease.diagnostics()
    assert diagnostics.last_error_category == "LEASE_INVALID"
    assert "User-Agent" not in repr(diagnostics)
    assert lease.metrics.acquire_attempts == 1


def test_defaults_and_no_history(tmp_path: Path) -> None:
    lease = make(tmp_path / "lease.sqlite3", "a", Clock())
    assert DEFAULT_TTL_SECONDS == 45.0
    lease.try_acquire()
    for _ in range(5):
        lease.heartbeat()
    connection = sqlite3.connect(tmp_path / "lease.sqlite3")
    assert connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 1
    assert not connection.execute("SELECT name FROM sqlite_master WHERE name LIKE '%history%'").fetchall()
    connection.close()


def test_owner_metadata_and_future_expiry_is_not_stolen(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "lease.sqlite3"
    owner = make(path, "owner-a", clock)
    assert owner.try_acquire().is_owner
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT lease_name,owner_id,pid,host_id,process_started_at,schema_version FROM leases"
    ).fetchone()
    assert row[0] == LEASE_NAME and row[1] == "owner-a" and row[3] == "host-test" and row[5] == 1
    connection.execute(
        "UPDATE leases SET expires_at=? WHERE lease_name=?",
        ((T0 + timedelta(days=365)).isoformat(), LEASE_NAME),
    )
    connection.commit()
    connection.close()
    assert make(path, "owner-b", clock).try_acquire().outcome is LeaseOutcome.DENIED


def test_busy_database_returns_bounded_error(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "lease.sqlite3"
    first = make(path, "a", clock, busy_timeout_ms=50)
    assert first.try_acquire().is_owner
    connection = sqlite3.connect(path)
    connection.execute("BEGIN EXCLUSIVE")
    try:
        second = make(path, "b", clock, busy_timeout_ms=50)
        result = second.try_acquire()
        assert result.outcome is LeaseOutcome.ERROR
        assert second.metrics.sqlite_busy_failures >= 1
    finally:
        connection.rollback()
        connection.close()


def test_invalid_clock_and_ttl_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make(tmp_path / "a.sqlite3", "a", Clock(), ttl_seconds=9)
    with pytest.raises(ValueError):
        SecNetworkOwnershipLease(tmp_path / "b.sqlite3", clock=lambda: datetime.now())

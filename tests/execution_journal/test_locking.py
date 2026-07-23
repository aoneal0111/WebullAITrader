from decimal import Decimal
import multiprocessing
import pytest
from app.execution_journal import *

def _hold_lock(path,policy_data,ready,release):
    policy=ExecutionJournalPolicy.from_dict(policy_data)
    with ExecutionJournalLock(path,policy):
        ready.set();release.wait(10)

def test_ownership_context_cleanup_and_leftover_file(tmp_path):
    path=tmp_path/"journal";lock=ExecutionJournalLock(path,ExecutionJournalPolicy())
    with pytest.raises(ExecutionJournalNotLockedError):lock.release()
    lock.acquire();assert lock.locked
    with pytest.raises(ExecutionJournalAlreadyLockedError):lock.acquire()
    lock.release();assert not lock.locked
    with pytest.raises(ExecutionJournalNotLockedError):lock.release()
    with pytest.raises(LookupError):
        with lock:raise LookupError("boom")
    assert not lock.locked
    assert lock.lock_path.exists()
    with ExecutionJournalLock(path,ExecutionJournalPolicy()):pass

def test_cross_process_timeout_and_reuse(tmp_path):
    ctx=multiprocessing.get_context("spawn");ready=ctx.Event();release=ctx.Event();path=tmp_path/"journal"
    holder_policy=ExecutionJournalPolicy(lock_timeout_seconds=Decimal("2"))
    process=ctx.Process(target=_hold_lock,args=(str(path),holder_policy.to_dict(),ready,release));process.start()
    assert ready.wait(10)
    short=ExecutionJournalPolicy(lock_timeout_seconds=Decimal("0.1"),lock_poll_interval_seconds=Decimal("0.01"))
    with pytest.raises(ExecutionJournalLockTimeoutError):ExecutionJournalLock(path,short).acquire()
    release.set();process.join(10)
    if process.is_alive():process.terminate();process.join(5)
    assert process.exitcode==0
    with ExecutionJournalLock(path,short):pass

def test_locking_disabled_preserves_behavior(tmp_path):
    from tests.execution_journal.helpers import authorization
    journal=JsonlExecutionJournal(tmp_path/"journal",ExecutionJournalPolicy(locking_enabled=False))
    journal.append_authorization(authorization());assert len(journal.load_records())==1

def test_lock_released_after_duplicate_size_and_append_failures(tmp_path,monkeypatch):
    from tests.execution_journal.helpers import authorization
    item=authorization();path=tmp_path/"journal";journal=JsonlExecutionJournal(path);journal.append_authorization(item)
    with pytest.raises(JournalDuplicateError):journal.append_authorization(item)
    with ExecutionJournalLock(path,journal.policy):pass
    other_path=tmp_path/"small";small=JsonlExecutionJournal(other_path,ExecutionJournalPolicy(maximum_record_bytes=1))
    with pytest.raises(JournalError):small.append_authorization(item)
    with ExecutionJournalLock(other_path,small.policy):pass
    failed=JsonlExecutionJournal(tmp_path/"failed");original=failed.storage.append
    monkeypatch.setattr(failed.storage,"append",lambda *args:(_ for _ in ()).throw(OSError("controlled append failure")))
    with pytest.raises(OSError):failed.append_authorization(item)
    monkeypatch.setattr(failed.storage,"append",original);failed.append_authorization(item)

def test_corruption_failure_releases_lock(tmp_path):
    path=tmp_path/"journal";path.write_bytes(b"{bad}\n");journal=JsonlExecutionJournal(path)
    with pytest.raises(JournalIntegrityError):journal.recover()
    with ExecutionJournalLock(path,journal.policy):pass

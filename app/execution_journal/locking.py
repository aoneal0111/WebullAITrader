from __future__ import annotations
import os,time
from pathlib import Path
from app.execution_journal.policies import ExecutionJournalPolicy

class ExecutionJournalLockError(ValueError):pass
class ExecutionJournalLockTimeoutError(ExecutionJournalLockError):pass
class ExecutionJournalAlreadyLockedError(ExecutionJournalLockError):pass
class ExecutionJournalNotLockedError(ExecutionJournalLockError):pass
class ExecutionJournalLockReleaseError(ExecutionJournalLockError):pass

class ExecutionJournalLock:
    """Exclusive OS lock; monotonic time is used only for bounded coordination."""
    def __init__(self,journal_path,policy:ExecutionJournalPolicy):
        if not isinstance(policy,ExecutionJournalPolicy):raise ValueError("policy must be ExecutionJournalPolicy")
        self.journal_path=Path(journal_path);self.lock_path=Path(str(self.journal_path)+policy.lock_file_suffix);self.policy=policy;self._file=None
    @property
    def locked(self):return self._file is not None
    def acquire(self):
        if self.locked:raise ExecutionJournalAlreadyLockedError(f"lock already held: {self.lock_path}")
        self.lock_path.parent.mkdir(parents=True,exist_ok=True);handle=open(self.lock_path,"a+b")
        if handle.seek(0,os.SEEK_END)==0:handle.write(b"0");handle.flush()
        deadline=time.monotonic()+float(self.policy.lock_timeout_seconds)
        while True:
            try:self._try_lock(handle);self._file=handle;return self
            except (BlockingIOError,OSError) as exc:
                if time.monotonic()>=deadline:
                    handle.close();raise ExecutionJournalLockTimeoutError(f"timed out locking journal={self.journal_path} lock={self.lock_path} timeout={self.policy.lock_timeout_seconds}") from exc
                time.sleep(min(float(self.policy.lock_poll_interval_seconds),max(0,deadline-time.monotonic())))
    def release(self):
        if not self.locked:raise ExecutionJournalNotLockedError(f"lock is not held: {self.lock_path}")
        handle=self._file
        try:self._unlock(handle)
        except OSError as exc:raise ExecutionJournalLockReleaseError(f"failed to release lock: {self.lock_path}") from exc
        finally:
            handle.close();self._file=None
    def __enter__(self):return self.acquire()
    def __exit__(self,*args):self.release();return False
    @staticmethod
    def _try_lock(handle):
        handle.seek(0)
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    @staticmethod
    def _unlock(handle):
        handle.seek(0)
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

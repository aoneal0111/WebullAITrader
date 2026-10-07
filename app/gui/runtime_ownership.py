"""Single-workstation ownership for the PAPER desktop runtime.

The lock is an OS-held byte lock.  It is released automatically if a process
dies, so stale lock files never require destructive cleanup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RuntimeOwnership:
    path: Path
    _handle: object

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        try:
            handle.close()
        except (OSError, ValueError):
            pass
        self._handle = None

    def __enter__(self) -> "RuntimeOwnership":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def acquire_runtime_ownership(path: Path) -> RuntimeOwnership | None:
    """Acquire one non-blocking runtime lock, without deleting stale files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    handle = path.open("r+", encoding="utf-8")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate(0)
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    except (OSError, IOError):
        try:
            handle.close()
        except OSError:
            pass
        return None
    return RuntimeOwnership(path, handle)

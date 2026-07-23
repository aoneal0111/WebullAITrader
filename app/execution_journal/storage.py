from __future__ import annotations
import os
from pathlib import Path
from app.execution_journal.models import canonical_json
class JournalError(ValueError):pass
class JournalDuplicateError(JournalError):pass
class JournalIntegrityError(JournalError):pass
class JsonlStorage:
    def __init__(self,path):self.path=Path(path)
    def read_bytes(self):return self.path.read_bytes() if self.path.exists() else b""
    def append(self,value,fsync_enabled,maximum_record_bytes):
        data=(canonical_json(value)+"\n").encode("utf-8")
        if len(data)>maximum_record_bytes:raise JournalError("record exceeds maximum_record_bytes")
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
        try:
            written=os.write(fd,data)
            if written!=len(data):raise JournalError("incomplete journal append")
            if fsync_enabled:os.fsync(fd)
        finally:os.close(fd)

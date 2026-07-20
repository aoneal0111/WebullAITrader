from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from app.operations.redaction import redact

SENSITIVE = frozenset(("password", "token", "access_token", "refresh_token", "account_id", "account_number", "app_secret", "client_secret", "authorization"))
class LogSink(Protocol):
    def emit(self, record: dict[str, object]) -> None: ...
@dataclass(frozen=True, slots=True)
class StructuredLogger:
    sink: LogSink
    def log(self, operation: str, status: str, **fields):
        record = {"operation": operation, "status": status}
        record.update(redact(fields))
        self.sink.emit(dict(sorted(record.items())))

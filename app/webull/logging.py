from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from app.operations.redaction import redact

SENSITIVE = frozenset(
    (
        "password",
        "token",
        "access_token",
        "refresh_token",
        "account_id",
        "account_number",
        "api_key",
        "app_key",
        "app_secret",
        "client_secret",
        "authorization",
        "signature",
        "x-app-key",
        "x-access-token",
        "x-signature",
        "x-signature-nonce",
        "api_secret",
        "cookie",
        "signed_headers",
    )
)
class LogSink(Protocol):
    def emit(self, record: dict[str, object]) -> None: ...
@dataclass(frozen=True, slots=True)
class StructuredLogger:
    sink: LogSink
    def log(self, operation: str, status: str, **fields):
        record = {"operation": operation, "status": status}
        record.update(redact(fields))
        self.sink.emit(dict(sorted(record.items())))


def sanitized_sdk_event(
    *,
    status: str,
    http_status: int | None = None,
    error_code: str | None = None,
    endpoint_path: str | None = None,
    capability: str | None = None,
    environment: str | None = None,
    request_id: str | None = None,
) -> dict[str, object]:
    """Build an allow-listed SDK diagnostic without accepting request dumps."""

    event: dict[str, object] = {"operation": "webull_sdk", "status": status}
    for key, value in (
        ("http_status", http_status),
        ("error_code", error_code),
        ("endpoint_path", endpoint_path),
        ("capability", capability),
        ("environment", environment),
        ("request_id", request_id),
    ):
        if value is not None:
            event[key] = value
    return dict(sorted(event.items()))

"""Safe, durable diagnostics for runtime lifecycle exceptions."""

from __future__ import annotations

import logging
from threading import current_thread, get_ident
import traceback


_SENSITIVE_MARKERS = (
    "ACCOUNT ID",
    "ACCOUNT_ID",
    "ACCOUNT NUMBER",
    "ACCOUNT_NUMBER",
    "APP KEY",
    "APP_KEY",
    "AUTHORIZATION HEADER",
    "PASSWORD",
    "SECRET",
    "SESSION ID",
    "SIGNATURE",
    "TOKEN",
)


def log_runtime_exception(
    logger: logging.Logger,
    error: Exception,
    *,
    event_type: str,
    lifecycle_phase: str,
    shutdown_requested: bool,
    primary: bool = True,
) -> None:
    """Emit an exception and traceback without exposing credential-like text."""

    sensitive = _exception_chain_contains_sensitive_text(error)
    safe_message = safe_exception_message(error, include_type=False)
    formatted_traceback = _format_traceback(error, redact_message=sensitive)
    thread = current_thread()
    logger.error(
        "event_type=%s lifecycle_phase=%s shutdown_requested=%s "
        "exception_role=%s exception_type=%s exception_message=%s "
        "thread_name=%s thread_id=%s\n%s",
        event_type,
        lifecycle_phase,
        shutdown_requested,
        "primary" if primary else "secondary",
        type(error).__name__,
        safe_message,
        thread.name,
        get_ident(),
        formatted_traceback.rstrip(),
    )


def safe_exception_message(
    error: Exception,
    *,
    include_type: bool = True,
) -> str:
    """Return a display-safe exception summary using the diagnostic policy."""

    message = str(error).strip()
    if _exception_chain_contains_sensitive_text(error):
        message = "[REDACTED]"
    elif not message:
        message = "<no message>"
    return f"{type(error).__name__}: {message}" if include_type else message


def _exception_chain_contains_sensitive_text(error: Exception) -> bool:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        detail = str(current).upper()
        if any(marker in detail for marker in _SENSITIVE_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _format_traceback(error: Exception, *, redact_message: bool) -> str:
    if not redact_message:
        return "".join(
            traceback.format_exception(
                type(error),
                error,
                error.__traceback__,
            )
        )

    frames = traceback.extract_tb(error.__traceback__)
    return (
        "Traceback (most recent call last):\n"
        f"{''.join(traceback.format_list(frames))}"
        f"{type(error).__name__}: [REDACTED]\n"
    )


__all__ = ["log_runtime_exception", "safe_exception_message"]

"""Bounded credential-redacted exception details; standard library only."""
from pathlib import Path
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


def safe_exception_details(error: BaseException) -> dict[str, object]:
    """Bounded cause chain, without locals, source text or absolute paths."""
    chain = []
    visited = set()
    sensitive = _exception_chain_contains_sensitive_text(error)
    current = error
    while current is not None and id(current) not in visited and len(chain) < 8:
        visited.add(id(current))
        chain.append({
            "exception_type": type(current).__name__,
            "message": "[REDACTED]" if sensitive else str(current)[:512],
            "frames": [dict(file=Path(f.filename).name, function=f.name, line=f.lineno)
                       for f in traceback.extract_tb(current.__traceback__)[-16:]],
        })
        current = current.__cause__ or current.__context__
    return {"chain": chain, "truncated": current is not None}


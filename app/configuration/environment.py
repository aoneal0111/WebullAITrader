"""Deterministic runtime environment resolution for scoped Webull identity."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values


MARKET_DATA_DOTENV_KEYS = (
    "WEBULL_MARKET_DATA_ENVIRONMENT",
    "WEBULL_MARKET_DATA_APP_KEY",
    "WEBULL_MARKET_DATA_APP_SECRET",
    "WEBULL_MARKET_DATA_API_BASE_URL",
    "WEBULL_MARKET_DATA_STREAM_URL",
)

SEC_EDGAR_DOTENV_KEYS = (
    "SEC_EDGAR_USER_AGENT",
    "SEC_EDGAR_FRESHNESS_DAYS",
    "SEC_EDGAR_TIMEOUT_SECONDS",
)

_RUNTIME_DOTENV_KEYS = MARKET_DATA_DOTENV_KEYS + SEC_EDGAR_DOTENV_KEYS

_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*="
)


def resolve_runtime_environment(
    process_environment: Mapping[str, str] | None = None,
    *,
    dotenv_path: str | Path = ".env",
) -> dict[str, str]:
    """Overlay scoped market-data and SEC settings over process values.

    Other operational, trading, execution, and risk settings retain normal
    process environment behavior.
    """

    resolved = dict(os.environ if process_environment is None else process_environment)
    path = Path(dotenv_path)
    if not path.is_file():
        return resolved
    duplicates = duplicate_dotenv_keys(path)
    ambiguous = tuple(sorted(set(duplicates).intersection(_RUNTIME_DOTENV_KEYS)))
    if ambiguous:
        raise ValueError(
            "duplicate scoped market-data settings in .env: " + ",".join(ambiguous)
        )
    file_values = dotenv_values(path)
    for name in _RUNTIME_DOTENV_KEYS:
        value = file_values.get(name)
        if value is not None:
            resolved[name] = str(value)
    return resolved


def duplicate_dotenv_keys(path: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            name = match.group(1)
            counts[name] = counts.get(name, 0) + 1
    return {name: count for name, count in counts.items() if count > 1}


__all__ = [
    "MARKET_DATA_DOTENV_KEYS",
    "SEC_EDGAR_DOTENV_KEYS",
    "duplicate_dotenv_keys",
    "resolve_runtime_environment",
]

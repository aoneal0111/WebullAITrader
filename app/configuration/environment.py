"""Deterministic runtime environment resolution for scoped Webull identity."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

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

SYMBOL_INTELLIGENCE_SEC_CANONICAL_KEYS = (
    "ATLAS_SEC_EDGAR_ENABLED",
    "ATLAS_SEC_EDGAR_USER_AGENT",
    "ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND",
    "ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS",
    "ATLAS_SEC_EDGAR_READ_TIMEOUT_SECONDS",
    "ATLAS_SEC_EDGAR_MAX_RETRIES",
    "ATLAS_SEC_EDGAR_BACKOFF_INITIAL_SECONDS",
    "ATLAS_SEC_EDGAR_BACKOFF_MAX_SECONDS",
    "ATLAS_SEC_EDGAR_FAILURE_COOLDOWN_SECONDS",
    "ATLAS_SEC_EDGAR_FRESHNESS_DAYS",
    "ATLAS_SEC_EDGAR_TICKER_REFRESH_SECONDS",
    "ATLAS_SEC_EDGAR_SUBMISSIONS_REFRESH_SECONDS",
    "ATLAS_SEC_EDGAR_MAX_TICKER_ENTRIES",
    "ATLAS_SEC_EDGAR_MAX_SUBMISSIONS_CACHE_ENTRIES",
    "ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED",
)

_SYMBOL_INTELLIGENCE_SEC_LEGACY_ALIASES = {
    "ATLAS_SEC_EDGAR_ENABLED": "SEC_EDGAR_ENABLED",
    "ATLAS_SEC_EDGAR_USER_AGENT": "SEC_EDGAR_USER_AGENT",
    "ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS": "SEC_EDGAR_TIMEOUT_SECONDS",
    "ATLAS_SEC_EDGAR_READ_TIMEOUT_SECONDS": "SEC_EDGAR_TIMEOUT_SECONDS",
    "ATLAS_SEC_EDGAR_FRESHNESS_DAYS": "SEC_EDGAR_FRESHNESS_DAYS",
}


@dataclass(frozen=True, slots=True)
class ResolvedSymbolIntelligenceSECEnvironment:
    """Resolved values plus non-sensitive provenance for precedence decisions."""

    values: Mapping[str, str] = field(repr=False)
    origins: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "origins", MappingProxyType(dict(self.origins)))

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name, default)

    def origin(self, name: str) -> str | None:
        return self.origins.get(name)

YAHOO_FINANCE_NEWS_DOTENV_KEYS = (
    "YAHOO_FINANCE_NEWS_ENABLED",
    "YAHOO_FINANCE_NEWS_FRESHNESS_MINUTES",
    "YAHOO_FINANCE_TIMEOUT_SECONDS",
    "YAHOO_FINANCE_NEWS_CACHE_TTL_SECONDS",
)

CNBC_NEWS_DOTENV_KEYS = (
    "CNBC_NEWS_ENABLED",
    "CNBC_NEWS_FRESHNESS_MINUTES",
    "CNBC_NEWS_TIMEOUT_SECONDS",
    "CNBC_NEWS_REFRESH_TTL_SECONDS",
    "CNBC_NEWS_FAILURE_COOLDOWN_SECONDS",
    "CNBC_NEWS_MAXIMUM_SNAPSHOT_AGE_SECONDS",
    "CNBC_NEWS_MAX_ITEMS",
    "CNBC_NEWS_MAX_PAYLOAD_BYTES",
)

MARKETWATCH_NEWS_DOTENV_KEYS = (
    "MARKETWATCH_NEWS_ENABLED",
    "MARKETWATCH_NEWS_FRESHNESS_MINUTES",
    "MARKETWATCH_NEWS_TIMEOUT_SECONDS",
    "MARKETWATCH_NEWS_REFRESH_TTL_SECONDS",
    "MARKETWATCH_NEWS_FAILURE_COOLDOWN_SECONDS",
    "MARKETWATCH_NEWS_MAXIMUM_SNAPSHOT_AGE_SECONDS",
    "MARKETWATCH_NEWS_MAX_ITEMS",
    "MARKETWATCH_NEWS_MAX_PAYLOAD_BYTES",
)

_RUNTIME_DOTENV_KEYS = (
    MARKET_DATA_DOTENV_KEYS
    + SEC_EDGAR_DOTENV_KEYS
    + YAHOO_FINANCE_NEWS_DOTENV_KEYS
    + CNBC_NEWS_DOTENV_KEYS
    + MARKETWATCH_NEWS_DOTENV_KEYS
)

_ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*="
)


def resolve_runtime_environment(
    process_environment: Mapping[str, str] | None = None,
    *,
    dotenv_path: str | Path = ".env",
) -> dict[str, str]:
    """Overlay scoped market-data and catalyst settings over process values.

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


def resolve_symbol_intelligence_sec_environment(
    process_environment: Mapping[str, str] | None = None,
    *,
    dotenv_path: str | Path | None = ".env",
) -> ResolvedSymbolIntelligenceSECEnvironment:
    """Resolve only new SEC settings with process-before-dotenv precedence."""

    process = dict(os.environ if process_environment is None else process_environment)
    file_values: Mapping[str, object] = {}
    if dotenv_path is not None:
        path = Path(dotenv_path)
        if path.is_file():
            relevant = set(SYMBOL_INTELLIGENCE_SEC_CANONICAL_KEYS)
            relevant.update(_SYMBOL_INTELLIGENCE_SEC_LEGACY_ALIASES.values())
            duplicates = set(duplicate_dotenv_keys(path)).intersection(relevant)
            if duplicates:
                raise ValueError(
                    "duplicate symbol-intelligence SEC settings in .env: "
                    + ",".join(sorted(duplicates))
                )
            file_values = dotenv_values(path)

    resolved: dict[str, str] = {}
    origins: dict[str, str] = {}
    for canonical in SYMBOL_INTELLIGENCE_SEC_CANONICAL_KEYS:
        legacy = _SYMBOL_INTELLIGENCE_SEC_LEGACY_ALIASES.get(canonical)
        candidates = (
            (canonical, process, "canonical_process"),
            (legacy, process, "legacy_process"),
            (canonical, file_values, "canonical_dotenv"),
            (legacy, file_values, "legacy_dotenv"),
        )
        for key, values, origin in candidates:
            if key is not None and key in values and values[key] is not None:
                resolved[canonical] = str(values[key])
                origins[canonical] = origin
                break
    return ResolvedSymbolIntelligenceSECEnvironment(resolved, origins)


def duplicate_dotenv_keys(path: str | Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            name = match.group(1)
            counts[name] = counts.get(name, 0) + 1
    return {name: count for name, count in counts.items() if count > 1}


__all__ = [
    "CNBC_NEWS_DOTENV_KEYS",
    "MARKET_DATA_DOTENV_KEYS",
    "MARKETWATCH_NEWS_DOTENV_KEYS",
    "SEC_EDGAR_DOTENV_KEYS",
    "SYMBOL_INTELLIGENCE_SEC_CANONICAL_KEYS",
    "YAHOO_FINANCE_NEWS_DOTENV_KEYS",
    "ResolvedSymbolIntelligenceSECEnvironment",
    "duplicate_dotenv_keys",
    "resolve_runtime_environment",
    "resolve_symbol_intelligence_sec_environment",
]

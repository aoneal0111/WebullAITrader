"""Offline SEC ticker-map parsing and immutable issuer identity contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
from enum import StrEnum
from typing import Any


_SYMBOL = re.compile(r"^[A-Z0-9](?:[A-Z0-9.-]{0,19}[A-Z0-9])?$")
MAX_MAP_BYTES = 16 * 1024 * 1024
MAX_MAP_ROWS = 50_000
MAX_CIK = 9_999_999_999


class SecTickerMapError(ValueError):
    """The complete SEC ticker map is invalid and must not be applied."""


class AmbiguousTickerMapError(SecTickerMapError):
    """Two different issuers claim one normalized lookup symbol."""


class SecResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


def normalize_sec_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise SecTickerMapError("SEC ticker must be text")
    value = symbol.strip().upper()
    if not value or len(value) > 20 or not _SYMBOL.fullmatch(value):
        raise SecTickerMapError("SEC ticker is malformed")
    normalized = value.replace(".", "-")
    if "--" in normalized or normalized.endswith("-") or normalized.startswith("-"):
        raise SecTickerMapError("SEC ticker punctuation is malformed")
    return normalized


def _cik(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise SecTickerMapError("SEC CIK is missing or malformed")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip(), 10)
    else:
        raise SecTickerMapError("SEC CIK is malformed")
    if result <= 0 or result > MAX_CIK:
        raise SecTickerMapError("SEC CIK is out of range")
    return result


def sec_issuer_id(cik: int) -> str:
    value = _cik(cik)
    return f"SEC_CIK:{value:010d}"


def sec_cik_path(cik: int) -> str:
    value = _cik(cik)
    return f"CIK{value:010d}.json"


@dataclass(frozen=True, slots=True)
class SecIssuerIdentity:
    normalized_symbol: str
    cik: int
    issuer_id: str
    canonical_ticker: str
    issuer_name: str | None
    exchange: str | None
    share_class: str | None
    source_revision: str
    observed_at: datetime
    verified: bool

    def __post_init__(self) -> None:
        normalized = normalize_sec_symbol(self.normalized_symbol)
        cik = _cik(self.cik)
        if self.issuer_id != sec_issuer_id(cik):
            raise ValueError("issuer_id must match CIK")
        if not isinstance(self.canonical_ticker, str) or not self.canonical_ticker.strip():
            raise ValueError("canonical_ticker is required")
        if not isinstance(self.source_revision, str) or not self.source_revision.strip():
            raise ValueError("source_revision is required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "normalized_symbol", normalized)
        object.__setattr__(self, "cik", cik)
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        object.__setattr__(self, "issuer_name", _optional(self.issuer_name, 256))
        object.__setattr__(self, "exchange", _optional(self.exchange, 32))
        object.__setattr__(self, "share_class", _optional(self.share_class, 128))


@dataclass(frozen=True, slots=True)
class SecIssuerResolution:
    requested_symbol: str
    normalized_symbol: str | None
    status: SecResolutionStatus
    identity: SecIssuerIdentity | None = None
    source_revision: str | None = None
    observed_at: datetime | None = None
    as_of: datetime | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SecTickerMap:
    source: str
    source_revision: str
    observed_at: datetime
    identities: tuple[SecIssuerIdentity, ...]

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not self.identities or len(self.identities) > MAX_MAP_ROWS:
            raise ValueError("ticker map must contain 1..50000 identities")


class SecIssuerIdentityResolver:
    """Bounded current identity cache; historical lookups remain repository-backed."""

    def __init__(self, repository: Any, *, max_entries: int = 32_768) -> None:
        if max_entries <= 0 or max_entries > 32_768:
            raise ValueError("max_entries must be in 1..32768")
        self._repository = repository
        self._max_entries = max_entries
        self._current: dict[str, SecIssuerIdentity] = {}

    @property
    def size(self) -> int:
        return len(self._current)

    def recover(self, *, limit: int | None = None) -> int:
        bound = self._max_entries if limit is None else min(limit, self._max_entries)
        identities = self._repository.recover_current_identities(limit=bound)
        if len(identities) > self._max_entries:
            raise ValueError("recovered identity map exceeds cache bound")
        self._current = {item.normalized_symbol: item for item in identities}
        return len(self._current)

    def resolve_current(self, symbol: str) -> SecIssuerResolution:
        normalized = normalize_sec_symbol(symbol)
        identity = self._current.get(normalized)
        if identity is None:
            return SecIssuerResolution(symbol, normalized, SecResolutionStatus.UNRESOLVED, reason="CACHE_MISS")
        return SecIssuerResolution(symbol, normalized, SecResolutionStatus.RESOLVED, identity=identity)

    def resolve_at(self, symbol: str, as_of: datetime) -> SecIssuerResolution:
        return self._repository.resolve_symbol_identity(symbol, as_of)


def _optional(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > maximum:
        raise SecTickerMapError("SEC text field exceeds bound")
    return text


def _rows(payload: object, *, max_entries: int) -> list[Mapping[str, object]]:
    if isinstance(payload, (bytes, bytearray)):
        if len(payload) > MAX_MAP_BYTES:
            raise SecTickerMapError("SEC ticker payload exceeds byte bound")
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise SecTickerMapError("SEC ticker payload is not valid JSON") from exc
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_MAP_BYTES:
            raise SecTickerMapError("SEC ticker payload exceeds byte bound")
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise SecTickerMapError("SEC ticker payload is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise SecTickerMapError("SEC ticker payload must be an object")
    try:
        if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > MAX_MAP_BYTES:
            raise SecTickerMapError("SEC ticker payload exceeds byte bound")
    except (TypeError, ValueError) as exc:
        raise SecTickerMapError("SEC ticker payload is not serializable") from exc
    data: object = payload.get("data", payload)
    if isinstance(data, Mapping):
        values = list(data.values())
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        values = list(data)
    else:
        raise SecTickerMapError("SEC ticker rows are malformed")
    if len(values) > min(max_entries, MAX_MAP_ROWS):
        raise SecTickerMapError("SEC ticker payload exceeds row bound")
    if not values:
        raise SecTickerMapError("SEC ticker payload is empty")
    if not all(isinstance(row, Mapping) for row in values):
        raise SecTickerMapError("SEC ticker row is malformed")
    return values  # type: ignore[return-value]


def parse_sec_ticker_map(
    payload: object,
    *,
    source: str,
    observed_at: datetime,
    max_entries: int = MAX_MAP_ROWS,
) -> SecTickerMap:
    if not source.strip():
        raise ValueError("source is required")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    rows = _rows(payload, max_entries=max_entries)
    candidates: dict[str, dict[str, object]] = {}
    for row in rows:
        raw_ticker = row.get("ticker")
        if not isinstance(raw_ticker, str) or not raw_ticker.strip():
            raise SecTickerMapError("SEC ticker is missing")
        normalized = normalize_sec_symbol(raw_ticker)
        cik = _cik(row.get("cik_str", row.get("cik")))
        record = {
            "normalized_symbol": normalized,
            "cik": cik,
            "canonical_ticker": raw_ticker.strip().upper(),
            "issuer_name": _optional(row.get("title", row.get("name")), 256),
            "exchange": _optional(row.get("exchange"), 32),
            "share_class": _optional(row.get("share_class", row.get("security_class")), 128),
        }
        previous = candidates.get(normalized)
        if previous is not None:
            if previous["cik"] != cik:
                raise AmbiguousTickerMapError(f"SEC ticker collision for {normalized}")
            # Same-CIK aliases are deterministic: retain lexicographically smallest source ticker.
            if tuple(str(record[key]) for key in record) < tuple(str(previous[key]) for key in previous):
                candidates[normalized] = record
        else:
            candidates[normalized] = record
    logical = tuple(sorted(candidates.values(), key=lambda item: (str(item["normalized_symbol"]), int(item["cik"]))))
    digest_payload = {
        "source": source,
        "records": logical,
    }
    revision = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    timestamp = observed_at.astimezone(UTC)
    identities = tuple(
        SecIssuerIdentity(
            normalized_symbol=str(item["normalized_symbol"]), cik=int(item["cik"]),
            issuer_id=sec_issuer_id(int(item["cik"])), canonical_ticker=str(item["canonical_ticker"]),
            issuer_name=item["issuer_name"], exchange=item["exchange"], share_class=item["share_class"],
            source_revision=revision, observed_at=timestamp, verified=True,
        ) for item in logical
    )
    return SecTickerMap(source=source, source_revision=revision, observed_at=timestamp, identities=identities)


__all__ = [
    "AmbiguousTickerMapError", "MAX_CIK", "MAX_MAP_BYTES", "MAX_MAP_ROWS",
    "SecIssuerIdentity", "SecIssuerIdentityResolver", "SecIssuerResolution", "SecResolutionStatus", "SecTickerMap",
    "SecTickerMapError", "normalize_sec_symbol", "parse_sec_ticker_map", "sec_cik_path", "sec_issuer_id",
]

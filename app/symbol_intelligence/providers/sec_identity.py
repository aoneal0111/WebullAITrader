"""Offline SEC ticker-map parsing and immutable issuer identity contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import re
from enum import StrEnum
from types import MappingProxyType
from threading import RLock
from typing import Any


_SYMBOL = re.compile(r"^[A-Z0-9](?:[A-Z0-9.-]{0,19}[A-Z0-9])?$")
MAX_MAP_BYTES = 16 * 1024 * 1024
MAX_MAP_ROWS = 50_000
MAX_CIK = 9_999_999_999
MAX_ROW_FIELDS = 64
_NO_USABLE_TICKERS = frozenset({"NONE"})


class SecTickerMapError(ValueError):
    """The complete SEC ticker map is invalid and must not be applied."""


class AmbiguousTickerMapError(SecTickerMapError):
    """Two different issuers claim one normalized lookup symbol."""


class SecParserFailureCategory(StrEnum):
    PAYLOAD_INVALID_JSON = "PAYLOAD_INVALID_JSON"
    DATA_CONTAINER_INVALID = "DATA_CONTAINER_INVALID"
    ROW_NOT_MAPPING = "ROW_NOT_MAPPING"
    TICKER_MISSING = "TICKER_MISSING"
    TICKER_NON_TEXT = "TICKER_NON_TEXT"
    TICKER_BLANK = "TICKER_BLANK"
    TICKER_TOO_LONG = "TICKER_TOO_LONG"
    TICKER_INVALID_FORMAT = "TICKER_INVALID_FORMAT"
    CIK_MISSING = "CIK_MISSING"
    CIK_BOOLEAN = "CIK_BOOLEAN"
    CIK_NON_INTEGER = "CIK_NON_INTEGER"
    CIK_OUT_OF_RANGE = "CIK_OUT_OF_RANGE"
    TITLE_TOO_LONG = "TITLE_TOO_LONG"
    EXCHANGE_TOO_LONG = "EXCHANGE_TOO_LONG"
    SHARE_CLASS_TOO_LONG = "SHARE_CLASS_TOO_LONG"
    NORMALIZED_TICKER_CIK_COLLISION = "NORMALIZED_TICKER_CIK_COLLISION"
    EMPTY_MAP = "EMPTY_MAP"
    ROW_COUNT_EXCEEDED = "ROW_COUNT_EXCEEDED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    PAYLOAD_NONSERIALIZABLE = "PAYLOAD_NONSERIALIZABLE"


class SecParserRowType(StrEnum):
    MAPPING = "MAPPING"
    SEQUENCE = "SEQUENCE"
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class SecParserDiagnostic:
    category: SecParserFailureCategory
    row_type: SecParserRowType | None = None
    row_index: int | None = None


def _parser_observe(sink: Any, category: SecParserFailureCategory,
                    *, row_type: SecParserRowType | None = None,
                    row_index: int | None = None) -> None:
    try:
        if callable(sink):
            sink(SecParserDiagnostic(category, row_type, row_index))
    except Exception:
        return None


def _row_type(value: object) -> SecParserRowType:
    if isinstance(value, Mapping):
        return SecParserRowType.MAPPING
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return SecParserRowType.SEQUENCE
    if isinstance(value, str):
        return SecParserRowType.STRING
    if isinstance(value, bool):
        return SecParserRowType.BOOLEAN
    if value is None:
        return SecParserRowType.NULL
    if isinstance(value, (int, float)):
        return SecParserRowType.NUMBER
    return SecParserRowType.OTHER


def _no_usable_ticker(value: object) -> bool:
    if not isinstance(value, str):
        return False
    token = value.strip().upper().rstrip(".").strip()
    return token in _NO_USABLE_TICKERS


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
        self._lock = RLock()
        self._current: Mapping[str, SecIssuerIdentity] = MappingProxyType({})

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._current)

    def recover(self, *, limit: int | None = None) -> int:
        bound = self._max_entries if limit is None else min(limit, self._max_entries)
        identities = self._repository.recover_current_identities(limit=bound)
        if len(identities) > self._max_entries:
            raise ValueError("recovered identity map exceeds cache bound")
        # Construct the complete snapshot before taking the lock.  A failed
        # repository read therefore leaves the last known-good snapshot
        # untouched, and readers observe either complete snapshot.
        snapshot = MappingProxyType({item.normalized_symbol: item for item in identities})
        with self._lock:
            self._current = snapshot
            return len(snapshot)

    def resolve_current(self, symbol: str) -> SecIssuerResolution:
        normalized = normalize_sec_symbol(symbol)
        with self._lock:
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


def _rows(payload: object, *, max_entries: int, diagnostic: Any = None) -> list[Mapping[str, object]]:
    if isinstance(payload, (bytes, bytearray)):
        if len(payload) > MAX_MAP_BYTES:
            _parser_observe(diagnostic, SecParserFailureCategory.PAYLOAD_TOO_LARGE)
            raise SecTickerMapError("SEC ticker payload exceeds byte bound")
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError) as exc:
            _parser_observe(diagnostic, SecParserFailureCategory.PAYLOAD_INVALID_JSON)
            raise SecTickerMapError("SEC ticker payload is not valid JSON") from exc
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_MAP_BYTES:
            _parser_observe(diagnostic, SecParserFailureCategory.PAYLOAD_TOO_LARGE)
            raise SecTickerMapError("SEC ticker payload exceeds byte bound")
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            _parser_observe(diagnostic, SecParserFailureCategory.PAYLOAD_INVALID_JSON)
            raise SecTickerMapError("SEC ticker payload is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        _parser_observe(diagnostic, SecParserFailureCategory.DATA_CONTAINER_INVALID,
                        row_type=_row_type(payload))
        raise SecTickerMapError("SEC ticker payload must be an object")
    try:
        if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > MAX_MAP_BYTES:
            _parser_observe(diagnostic, SecParserFailureCategory.PAYLOAD_TOO_LARGE)
            raise SecTickerMapError("SEC ticker payload exceeds byte bound")
    except (TypeError, ValueError) as exc:
        _parser_observe(diagnostic, SecParserFailureCategory.PAYLOAD_NONSERIALIZABLE)
        raise SecTickerMapError("SEC ticker payload is not serializable") from exc
    if "fields" in payload:
        fields = payload.get("fields")
        data = payload.get("data")
        if (
            not isinstance(fields, Sequence)
            or isinstance(fields, (str, bytes, bytearray))
            or not isinstance(data, Sequence)
            or isinstance(data, (str, bytes, bytearray))
        ):
            _parser_observe(diagnostic, SecParserFailureCategory.DATA_CONTAINER_INVALID,
                            row_type=_row_type(data))
            raise SecTickerMapError("SEC ticker rows are malformed")
        normalized_fields: list[str] = []
        seen: set[str] = set()
        for field in fields:
            if not isinstance(field, str) or not field.strip():
                _parser_observe(diagnostic, SecParserFailureCategory.DATA_CONTAINER_INVALID,
                                row_type=_row_type(field))
                raise SecTickerMapError("SEC ticker fields are malformed")
            normalized = field.strip().lower()
            if normalized in seen:
                _parser_observe(diagnostic, SecParserFailureCategory.DATA_CONTAINER_INVALID)
                raise SecTickerMapError("SEC ticker fields are malformed")
            seen.add(normalized)
            normalized_fields.append(normalized)
        required = {"cik", "name", "ticker", "exchange"}
        if not required.issubset(seen):
            _parser_observe(diagnostic, SecParserFailureCategory.DATA_CONTAINER_INVALID)
            raise SecTickerMapError("SEC ticker fields are malformed")
        positions = {name: normalized_fields.index(name) for name in required}
        values = list(data)
        if len(values) > min(max_entries, MAX_MAP_ROWS):
            _parser_observe(diagnostic, SecParserFailureCategory.ROW_COUNT_EXCEEDED,
                            row_index=min(len(values), MAX_MAP_ROWS))
            raise SecTickerMapError("SEC ticker payload exceeds row bound")
        if not values:
            _parser_observe(diagnostic, SecParserFailureCategory.EMPTY_MAP)
            raise SecTickerMapError("SEC ticker payload is empty")
        adapted: list[Mapping[str, object]] = []
        required_width = max(positions.values()) + 1
        for index, row in enumerate(values):
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
                _parser_observe(diagnostic, SecParserFailureCategory.ROW_NOT_MAPPING,
                                row_type=_row_type(row), row_index=min(index, MAX_MAP_ROWS - 1))
                raise SecTickerMapError("SEC ticker row is malformed")
            if len(row) > MAX_ROW_FIELDS or len(row) < required_width:
                _parser_observe(diagnostic, SecParserFailureCategory.ROW_NOT_MAPPING,
                                row_type=SecParserRowType.SEQUENCE, row_index=min(index, MAX_MAP_ROWS - 1))
                raise SecTickerMapError("SEC ticker row is malformed")
            adapted.append({field: row[position] for field, position in positions.items()})
        return adapted

    data: object = payload.get("data", payload)
    if isinstance(data, Mapping):
        values = list(data.values())
    elif isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        values = list(data)
    else:
        _parser_observe(diagnostic, SecParserFailureCategory.DATA_CONTAINER_INVALID,
                        row_type=_row_type(data))
        raise SecTickerMapError("SEC ticker rows are malformed")
    if len(values) > min(max_entries, MAX_MAP_ROWS):
        _parser_observe(diagnostic, SecParserFailureCategory.ROW_COUNT_EXCEEDED,
                        row_index=min(len(values), MAX_MAP_ROWS))
        raise SecTickerMapError("SEC ticker payload exceeds row bound")
    if not values:
        _parser_observe(diagnostic, SecParserFailureCategory.EMPTY_MAP)
        raise SecTickerMapError("SEC ticker payload is empty")
    for index, row in enumerate(values):
        if not isinstance(row, Mapping):
            _parser_observe(diagnostic, SecParserFailureCategory.ROW_NOT_MAPPING,
                            row_type=_row_type(row), row_index=min(index, MAX_MAP_ROWS - 1))
            raise SecTickerMapError("SEC ticker row is malformed")
    return values  # type: ignore[return-value]


def parse_sec_ticker_map(
    payload: object,
    *,
    source: str,
    observed_at: datetime,
    max_entries: int = MAX_MAP_ROWS,
    diagnostic: Any = None,
) -> SecTickerMap:
    if not source.strip():
        raise ValueError("source is required")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    rows = _rows(payload, max_entries=max_entries, diagnostic=diagnostic)
    candidates: dict[str, dict[str, object]] = {}
    for index, row in enumerate(rows):
        raw_ticker = row.get("ticker")
        if raw_ticker is None:
            _parser_observe(diagnostic, SecParserFailureCategory.TICKER_MISSING,
                            row_type=SecParserRowType.NULL, row_index=min(index, MAX_MAP_ROWS - 1))
            raise SecTickerMapError("SEC ticker is missing")
        if not isinstance(raw_ticker, str):
            _parser_observe(diagnostic, SecParserFailureCategory.TICKER_NON_TEXT,
                            row_type=_row_type(raw_ticker), row_index=min(index, MAX_MAP_ROWS - 1))
            raise SecTickerMapError("SEC ticker is missing")
        if not raw_ticker.strip():
            _parser_observe(diagnostic, SecParserFailureCategory.TICKER_BLANK,
                            row_index=min(index, MAX_MAP_ROWS - 1))
            raise SecTickerMapError("SEC ticker is missing")
        no_usable_ticker = _no_usable_ticker(raw_ticker)
        if not no_usable_ticker:
            try:
                normalized = normalize_sec_symbol(raw_ticker)
            except SecTickerMapError:
                category = (
                    SecParserFailureCategory.TICKER_TOO_LONG
                    if len(raw_ticker.strip()) > 20
                    else SecParserFailureCategory.TICKER_INVALID_FORMAT
                )
                _parser_observe(diagnostic, category, row_index=min(index, MAX_MAP_ROWS - 1))
                raise
        cik_value = row.get("cik_str", row.get("cik"))
        if no_usable_ticker and cik_value is None:
            continue
        if cik_value is None:
            _parser_observe(diagnostic, SecParserFailureCategory.CIK_MISSING,
                            row_index=min(index, MAX_MAP_ROWS - 1))
        elif isinstance(cik_value, bool):
            _parser_observe(diagnostic, SecParserFailureCategory.CIK_BOOLEAN,
                            row_index=min(index, MAX_MAP_ROWS - 1))
        elif not isinstance(cik_value, int) and not (isinstance(cik_value, str) and cik_value.strip().isdigit()):
            _parser_observe(diagnostic, SecParserFailureCategory.CIK_NON_INTEGER,
                            row_index=min(index, MAX_MAP_ROWS - 1))
        else:
            try:
                parsed_cik = int(cik_value)
            except (TypeError, ValueError):
                parsed_cik = None
            if parsed_cik is not None and (parsed_cik <= 0 or parsed_cik > MAX_CIK):
                _parser_observe(diagnostic, SecParserFailureCategory.CIK_OUT_OF_RANGE,
                                row_index=min(index, MAX_MAP_ROWS - 1))
        cik = _cik(cik_value)
        for field, maximum, category in (
            ("title", 256, SecParserFailureCategory.TITLE_TOO_LONG),
            ("exchange", 32, SecParserFailureCategory.EXCHANGE_TOO_LONG),
            ("share_class", 128, SecParserFailureCategory.SHARE_CLASS_TOO_LONG),
        ):
            value = row.get(field)
            if field == "title" and value is None:
                value = row.get("name")
            if field == "share_class" and value is None:
                value = row.get("security_class")
            if value is not None and len(str(value).strip()) > maximum:
                _parser_observe(diagnostic, category, row_index=min(index, MAX_MAP_ROWS - 1))
        if no_usable_ticker:
            continue
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
                _parser_observe(
                    diagnostic, SecParserFailureCategory.NORMALIZED_TICKER_CIK_COLLISION,
                    row_index=min(index, MAX_MAP_ROWS - 1),
                )
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
    "SecParserDiagnostic", "SecParserFailureCategory", "SecParserRowType",
    "SecTickerMapError", "normalize_sec_symbol", "parse_sec_ticker_map", "sec_cik_path", "sec_issuer_id",
]

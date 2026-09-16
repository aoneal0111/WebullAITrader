"""Offline normalization of SEC submissions into Symbol Intelligence facts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date
from enum import StrEnum
import json
import re
from typing import Any

from app.symbol_intelligence.models import DecayClass, EventType, IntelligenceEvent

from .sec_identity import SecIssuerIdentity, SecResolutionStatus, sec_issuer_id


SEC_SOURCE = "SEC_EDGAR"
SEC_SUBMISSIONS_PARSER_VERSION = "SEC_SUBMISSIONS_V1"
MAX_SUBMISSIONS_BYTES = 8 * 1024 * 1024
DEFAULT_RECENT_ROW_LIMIT = 4_096
MAX_RECENT_ROW_LIMIT = 8_192

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_COMPACT_ACCESSION = re.compile(r"^\d{18}$")
_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_FORMS = frozenset({
    "8-K", "8-K/A", "6-K", "6-K/A", "S-1", "S-1/A", "S-3", "S-3/A",
    "424B1", "424B2", "424B3", "424B4", "424B5", "EFFECT", "10-Q", "10-Q/A",
    "10-K", "10-K/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
})


class SecFilingNormalizationFailureKind(StrEnum):
    INVALID_JSON_STRUCTURE = "INVALID_JSON_STRUCTURE"
    CIK_MISMATCH = "CIK_MISMATCH"
    MISALIGNED_ARRAYS = "MISALIGNED_ARRAYS"
    INVALID_ACCESSION = "INVALID_ACCESSION"
    INVALID_FORM = "INVALID_FORM"
    INVALID_ACCEPTANCE_TIME = "INVALID_ACCEPTANCE_TIME"
    UNSUPPORTED_FORM = "UNSUPPORTED_FORM"
    INVALID_DOCUMENT_REFERENCE = "INVALID_DOCUMENT_REFERENCE"
    ROW_LIMIT_EXCEEDED = "ROW_LIMIT_EXCEEDED"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    DUPLICATE_ACCESSION_CONFLICT = "DUPLICATE_ACCESSION_CONFLICT"


@dataclass(frozen=True, slots=True)
class SecFilingNormalizationDiagnostics:
    rows_seen: int = 0
    rows_emitted: int = 0
    rows_rejected: int = 0
    rejection_counts: tuple[tuple[str, int], ...] = ()
    identity_status: str = "RESOLVED"
    cik_match: bool = True
    parser_version: str = SEC_SUBMISSIONS_PARSER_VERSION


@dataclass(frozen=True, slots=True)
class SecFilingNormalizationResult:
    events: tuple[IntelligenceEvent, ...] = ()
    diagnostics: SecFilingNormalizationDiagnostics = field(default_factory=SecFilingNormalizationDiagnostics)
    failure: SecFilingNormalizationFailureKind | None = None


def normalize_accession(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or any(ch.isspace() for ch in value):
        raise ValueError("invalid accession")
    if _ACCESSION.fullmatch(value):
        return value
    if _COMPACT_ACCESSION.fullmatch(value):
        return f"{value[:10]}-{value[10:12]}-{value[12:]}"
    raise ValueError("invalid accession")


class SecFilingFactNormalizer:
    """Convert one bounded, already-acquired submissions payload into raw facts."""

    def __init__(self, *, recent_row_limit: int = DEFAULT_RECENT_ROW_LIMIT, parser_version: str = SEC_SUBMISSIONS_PARSER_VERSION) -> None:
        if isinstance(recent_row_limit, bool) or not isinstance(recent_row_limit, int) or not 1 <= recent_row_limit <= MAX_RECENT_ROW_LIMIT:
            raise ValueError("recent_row_limit must be in 1..8192")
        if not isinstance(parser_version, str) or not parser_version.strip():
            raise ValueError("parser_version is required")
        self._recent_row_limit = recent_row_limit
        self._parser_version = parser_version.strip()

    def normalize(self, identity: SecIssuerIdentity | None, payload: bytes | bytearray | Mapping[str, Any], observed_at: datetime) -> SecFilingNormalizationResult:
        if identity is None or getattr(identity, "status", SecResolutionStatus.RESOLVED) is not SecResolutionStatus.RESOLVED:
            return self._failure(SecFilingNormalizationFailureKind.IDENTITY_UNRESOLVED, identity_status="UNRESOLVED")
        if not isinstance(identity, SecIssuerIdentity):
            return self._failure(SecFilingNormalizationFailureKind.IDENTITY_UNRESOLVED, identity_status="UNRESOLVED")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return self._failure(SecFilingNormalizationFailureKind.INVALID_ACCEPTANCE_TIME)
        observed = observed_at.astimezone(UTC)
        try:
            data = self._decode(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return self._failure(SecFilingNormalizationFailureKind.INVALID_JSON_STRUCTURE)
        if not isinstance(data, Mapping):
            return self._failure(SecFilingNormalizationFailureKind.INVALID_JSON_STRUCTURE)
        try:
            payload_cik = _parse_cik(data.get("cik"))
        except ValueError:
            return self._failure(SecFilingNormalizationFailureKind.CIK_MISMATCH, cik_match=False)
        if payload_cik != identity.cik:
            return self._failure(SecFilingNormalizationFailureKind.CIK_MISMATCH, cik_match=False)
        filings = data.get("filings")
        recent = filings.get("recent") if isinstance(filings, Mapping) else None
        if not isinstance(recent, Mapping):
            return self._failure(SecFilingNormalizationFailureKind.INVALID_JSON_STRUCTURE)
        required = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "primaryDocument")
        arrays: dict[str, list[Any]] = {}
        for name in required:
            value = recent.get(name)
            if not isinstance(value, list):
                return self._failure(SecFilingNormalizationFailureKind.MISALIGNED_ARRAYS)
            arrays[name] = value
        count = len(arrays["accessionNumber"])
        if count > self._recent_row_limit:
            return self._failure(SecFilingNormalizationFailureKind.ROW_LIMIT_EXCEEDED, rows_seen=count)
        if any(len(value) != count for value in arrays.values()):
            return self._failure(SecFilingNormalizationFailureKind.MISALIGNED_ARRAYS, rows_seen=count)
        optional: dict[str, list[Any] | None] = {}
        for name in ("reportDate", "act", "fileNumber", "filmNumber", "items", "size", "isXBRL", "isInlineXBRL", "primaryDocDescription"):
            value = recent.get(name)
            if value is None:
                optional[name] = None
            elif isinstance(value, list) and len(value) in (0, count):
                optional[name] = value
            else:
                return self._failure(SecFilingNormalizationFailureKind.MISALIGNED_ARRAYS, rows_seen=count)
        events: list[IntelligenceEvent] = []
        reasons: Counter[str] = Counter()
        seen: dict[str, IntelligenceEvent] = {}
        for index in range(count):
            try:
                event = self._row(identity, observed, arrays, optional, index)
            except _RowError as error:
                reasons[error.kind.value] += 1
                continue
            previous = seen.get(event.source_id or "")
            if previous is not None and previous != event:
                return self._failure(SecFilingNormalizationFailureKind.DUPLICATE_ACCESSION_CONFLICT, rows_seen=count)
            if previous is None:
                seen[event.source_id or ""] = event
                events.append(event)
        events.sort(key=lambda item: (item.published_at, item.source_id or "", item.event_id))
        diagnostics = SecFilingNormalizationDiagnostics(count, len(events), count - len(events), tuple(sorted(reasons.items())), "RESOLVED", True, self._parser_version)
        return SecFilingNormalizationResult(tuple(events), diagnostics)

    def _decode(self, payload: bytes | bytearray | Mapping[str, Any]) -> object:
        if isinstance(payload, (bytes, bytearray)):
            if len(payload) > MAX_SUBMISSIONS_BYTES:
                raise ValueError("payload too large")
            return json.loads(payload)
        if isinstance(payload, Mapping):
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if len(encoded) > MAX_SUBMISSIONS_BYTES:
                raise ValueError("payload too large")
            return payload
        raise TypeError("payload must be JSON bytes or object")

    def _row(self, identity: SecIssuerIdentity, observed: datetime, arrays: dict[str, list[Any]], optional: dict[str, list[Any] | None], index: int) -> IntelligenceEvent:
        try:
            accession = normalize_accession(arrays["accessionNumber"][index])
        except ValueError as exc:
            raise _RowError(SecFilingNormalizationFailureKind.INVALID_ACCESSION) from exc
        raw_form = arrays["form"][index]
        if not isinstance(raw_form, str) or not raw_form.strip():
            raise _RowError(SecFilingNormalizationFailureKind.INVALID_FORM)
        form = " ".join(raw_form.strip().upper().split())
        if form not in _FORMS:
            raise _RowError(SecFilingNormalizationFailureKind.UNSUPPORTED_FORM)
        accepted = arrays["acceptanceDateTime"][index]
        try:
            published = _parse_acceptance(accepted)
        except ValueError as exc:
            raise _RowError(SecFilingNormalizationFailureKind.INVALID_ACCEPTANCE_TIME) from exc
        if published > observed:
            raise _RowError(SecFilingNormalizationFailureKind.INVALID_ACCEPTANCE_TIME)
        filing_date = arrays["filingDate"][index]
        if not isinstance(filing_date, str):
            raise _RowError(SecFilingNormalizationFailureKind.INVALID_JSON_STRUCTURE)
        try:
            date.fromisoformat(filing_date.strip())
        except ValueError as exc:
            raise _RowError(SecFilingNormalizationFailureKind.INVALID_JSON_STRUCTURE) from exc
        metadata: dict[str, Any] = {"accession_number": accession, "filing_date": filing_date.strip(), "form": form}
        report = _optional_at(optional["reportDate"], index)
        if report:
            metadata["report_date"] = report
        items = _items(_optional_at(optional["items"], index))
        if items:
            metadata["item_codes"] = items
        document = arrays["primaryDocument"][index]
        reference = None
        if isinstance(document, str) and document.strip() and ".." not in document and _DOCUMENT.fullmatch(document.strip()):
            reference = f"https://www.sec.gov/Archives/edgar/data/{identity.cik}/{accession.replace('-', '')}/{document.strip()}"
            metadata["primary_document"] = document.strip()
        description = _optional_at(optional["primaryDocDescription"], index)
        headline = description[:512] if description else f"SEC {form} filing"
        try:
            return IntelligenceEvent(
                event_id=f"SEC_EDGAR:{accession}", symbol=identity.normalized_symbol, event_type=EventType.SEC_FILING,
                event_subtype=form, source=SEC_SOURCE, source_id=accession, issuer_id=sec_issuer_id(identity.cik),
                published_at=published, observed_at=observed, verified=True, decay_class=DecayClass.STRUCTURAL,
                source_parser_version=self._parser_version, headline=headline, source_reference=reference, metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            raise _RowError(SecFilingNormalizationFailureKind.INVALID_DOCUMENT_REFERENCE) from exc

    def _failure(self, kind: SecFilingNormalizationFailureKind, *, identity_status: str = "RESOLVED", cik_match: bool = True, rows_seen: int = 0) -> SecFilingNormalizationResult:
        diagnostics = SecFilingNormalizationDiagnostics(rows_seen=rows_seen, rows_rejected=rows_seen, identity_status=identity_status, cik_match=cik_match, parser_version=self._parser_version)
        return SecFilingNormalizationResult((), diagnostics, kind)


@dataclass(frozen=True, slots=True)
class _RowError(Exception):
    kind: SecFilingNormalizationFailureKind


def _parse_cik(value: object) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError("cik")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise ValueError("cik")
    if result <= 0 or result > 9_999_999_999:
        raise ValueError("cik")
    return result


def _parse_acceptance(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("acceptance")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("acceptance")
    return parsed.astimezone(UTC)


def _optional_at(values: list[Any] | None, index: int) -> Any:
    return None if values is None or not values else values[index]


def _items(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 64:
        return ()
    result = tuple(str(item).strip() for item in value if isinstance(item, str) and str(item).strip() and len(str(item).strip()) <= 32)
    return tuple(sorted(set(result)))


__all__ = [
    "DEFAULT_RECENT_ROW_LIMIT", "MAX_RECENT_ROW_LIMIT", "MAX_SUBMISSIONS_BYTES",
    "SEC_SOURCE", "SEC_SUBMISSIONS_PARSER_VERSION", "SecFilingFactNormalizer",
    "SecFilingNormalizationDiagnostics", "SecFilingNormalizationFailureKind",
    "SecFilingNormalizationResult", "normalize_accession",
]

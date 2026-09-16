"""Offline SEC catalyst compatibility over Symbol Intelligence facts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import re
from typing import Any, Callable

from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.symbol_intelligence import SourceAvailability
from app.symbol_intelligence.providers.sec_identity import (
    SecResolutionStatus,
    normalize_sec_symbol,
    sec_issuer_id,
)
from app.symbol_intelligence.providers.sec_filings import normalize_accession

from .models import CatalystEvidence


_SEC_SOURCE = "SEC_EDGAR"
_MAX_FACTS = 32
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_DOCUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SUPPORTED_FORMS = frozenset({
    "8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A",
    "S-1", "S-1/A", "S-3", "S-3/A", "SC 13D", "SC 13D/A",
    "SC 13G", "SC 13G/A",
})


class SecSymbolIntelligenceCatalystAdapter:
    """Read-only adapter retaining the legacy SEC evidence contract."""

    name = _SEC_SOURCE

    def __init__(
        self,
        repository: Any,
        *,
        freshness_days: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if freshness_days < 0:
            raise ValueError("freshness_days must not be negative")
        self._repository = repository
        self._freshness_days = freshness_days
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_evidence(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        try:
            normalized = normalize_sec_symbol(symbol)
        except (TypeError, ValueError):
            return self._negative(symbol, CatalystStatus.FALSE)
        try:
            cutoff = self._utc(as_of if as_of is not None else self._clock())
            state = self._repository.get_source_state(_SEC_SOURCE)
            if state is None:
                return self._negative(normalized, CatalystStatus.UNKNOWN)
            if state.availability is SourceAvailability.UNAVAILABLE:
                return self._negative(normalized, CatalystStatus.UNAVAILABLE)
            if state.availability is not SourceAvailability.AVAILABLE:
                return self._negative(normalized, CatalystStatus.UNKNOWN)
            # Source state is current-only.  Do not apply a state observed after
            # a historical cutoff as if it were known at that time.
            if as_of is not None and state.observed_at > cutoff:
                return self._negative(normalized, CatalystStatus.UNKNOWN)
            resolution = self._repository.resolve_symbol_identity(normalized, cutoff)
            if resolution.status is not SecResolutionStatus.RESOLVED or resolution.identity is None:
                return self._negative(normalized, CatalystStatus.UNKNOWN)
            events = self._repository.recent_sec_events_by_issuer(
                resolution.identity.issuer_id, limit=_MAX_FACTS, fact_cutoff=cutoff,
            )
            filing = self._select(events, cutoff)
            if filing is None:
                return self._negative(normalized, CatalystStatus.FALSE)
            event, form, accession, document, cik = filing
            return CatalystEvidence(
                symbol=normalized,
                catalyst_type=CatalystType.SEC_FILING,
                status=CatalystStatus.TRUE,
                headline=f"SEC {form} filing",
                source=_SEC_SOURCE,
                published_at=event.published_at,
                source_url=_ARCHIVE_URL.format(
                    cik=cik, accession=accession.replace("-", ""), document=document,
                ),
                provider_event_id=accession,
                canonical_event_id=f"sec-filing:{accession.casefold()}",
            )
        except Exception:
            return self._negative(normalized, CatalystStatus.UNKNOWN)

    def _select(self, events: tuple[Any, ...], cutoff: datetime) -> tuple[Any, str, str, str, int] | None:
        candidates: list[tuple[Any, str, str, str, int]] = []
        for event in events:
            try:
                if event.source != _SEC_SOURCE or event.event_type.value != "SEC_FILING":
                    continue
                if event.verified is not True:
                    raise ValueError("unverified SEC fact")
                form = " ".join(event.event_subtype.strip().upper().split())
                if form not in _SUPPORTED_FORMS and not re.fullmatch(r"424B[A-Z0-9-]*(?:/A)?", form):
                    continue
                metadata = event.metadata
                accession = normalize_accession(str(event.source_id or metadata["accession_number"]))
                filing_date = date.fromisoformat(str(metadata["filing_date"]).strip())
                document = str(metadata["primary_document"]).strip()
                if not accession or not _DOCUMENT.fullmatch(document) or ".." in document:
                    return None
                if filing_date < (cutoff - timedelta(days=self._freshness_days)).date() or filing_date > cutoff.date():
                    continue
                if event.published_at > cutoff or event.observed_at > cutoff:
                    continue
                issuer_text = str(event.issuer_id or "")
                match = re.fullmatch(r"SEC_CIK:(\d{1,})", issuer_text)
                if match is None or sec_issuer_id(int(match.group(1))) != issuer_text:
                    return None
                candidates.append((event, form, accession, document, int(match.group(1))))
            except Exception:
                raise
        return max(candidates, key=lambda item: (item[0].published_at, item[2]), default=None)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return value.astimezone(UTC)

    def _negative(self, symbol: str, status: CatalystStatus) -> CatalystEvidence:
        normalized = str(symbol).strip().upper() or "UNKNOWN"
        return CatalystEvidence(
            symbol=normalized, catalyst_type=CatalystType.NONE,
            status=status, source=_SEC_SOURCE,
        )


__all__ = ["SecSymbolIntelligenceCatalystAdapter"]

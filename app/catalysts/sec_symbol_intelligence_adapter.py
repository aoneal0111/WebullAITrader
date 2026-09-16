"""Offline SEC catalyst compatibility over Symbol Intelligence facts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.symbol_intelligence import SecIssuerAcquisitionStatus, SourceAvailability
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


class AdapterReadiness(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    ERROR = "ERROR"


class AdapterReadinessReason(StrEnum):
    SOURCE_STATE_MISSING = "SOURCE_STATE_MISSING"
    SOURCE_STATE_UNAVAILABLE = "SOURCE_STATE_UNAVAILABLE"
    HISTORICAL_SOURCE_HEALTH_UNKNOWN = "HISTORICAL_SOURCE_HEALTH_UNKNOWN"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    ISSUER_NOT_ACQUIRED = "ISSUER_NOT_ACQUIRED"
    ISSUER_ACQUISITION_INCOMPLETE = "ISSUER_ACQUISITION_INCOMPLETE"
    ISSUER_ACQUISITION_STALE = "ISSUER_ACQUISITION_STALE"
    MALFORMED_FACT = "MALFORMED_FACT"
    UNVERIFIED_FACT = "UNVERIFIED_FACT"
    REPOSITORY_ERROR = "REPOSITORY_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class SecCatalystAdapterEvaluation:
    evidence: CatalystEvidence
    readiness: AdapterReadiness
    reason: AdapterReadinessReason | None = None


class _UnverifiedFactError(ValueError):
    pass


class _MalformedFactError(ValueError):
    pass


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
        return self.evaluate(symbol, as_of=as_of).evidence

    def evaluate(
        self,
        symbol: str,
        *,
        as_of: datetime | None = None,
    ) -> SecCatalystAdapterEvaluation:
        """Return compatibility evidence plus typed readiness provenance."""
        try:
            normalized = normalize_sec_symbol(symbol)
        except (TypeError, ValueError):
            return SecCatalystAdapterEvaluation(
                self._negative(symbol, CatalystStatus.FALSE), AdapterReadiness.READY
            )
        try:
            cutoff = self._utc(as_of if as_of is not None else self._clock())
        except Exception:
            return SecCatalystAdapterEvaluation(
                self._negative(normalized, CatalystStatus.UNKNOWN),
                AdapterReadiness.ERROR, AdapterReadinessReason.INTERNAL_ERROR,
            )
        try:
            state = self._repository.get_source_state(_SEC_SOURCE)
        except Exception:
            return SecCatalystAdapterEvaluation(
                self._negative(normalized, CatalystStatus.UNKNOWN),
                AdapterReadiness.ERROR, AdapterReadinessReason.REPOSITORY_ERROR,
            )
        if state is None:
            return SecCatalystAdapterEvaluation(
                self._negative(normalized, CatalystStatus.UNKNOWN),
                AdapterReadiness.NOT_READY, AdapterReadinessReason.SOURCE_STATE_MISSING,
            )
        try:
            if state.availability is SourceAvailability.UNAVAILABLE:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNAVAILABLE),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.SOURCE_STATE_UNAVAILABLE,
                )
            if state.availability is not SourceAvailability.AVAILABLE:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.SOURCE_STATE_MISSING,
                )
            # Source state is current-only.  Do not apply a state observed after
            # a historical cutoff as if it were known at that time.
            if as_of is not None and state.observed_at > cutoff:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.HISTORICAL_SOURCE_HEALTH_UNKNOWN,
                )
            resolution = self._repository.resolve_symbol_identity(normalized, cutoff)
            if resolution.status is SecResolutionStatus.UNRESOLVED:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.IDENTITY_UNRESOLVED,
                )
            if resolution.status is SecResolutionStatus.AMBIGUOUS:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.IDENTITY_AMBIGUOUS,
                )
            if resolution.status is not SecResolutionStatus.RESOLVED or resolution.identity is None:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.IDENTITY_UNRESOLVED,
                )
            issuer_state = self._repository.get_sec_issuer_acquisition_state(
                resolution.identity.issuer_id, as_of=cutoff,
            )
            if issuer_state is None:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.ISSUER_NOT_ACQUIRED,
                )
            if issuer_state.last_complete_observation_at is None:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN), AdapterReadiness.NOT_READY,
                    AdapterReadinessReason.ISSUER_ACQUISITION_INCOMPLETE,
                )
            if issuer_state.last_complete_observation_at > cutoff:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.ISSUER_ACQUISITION_INCOMPLETE,
                )
            if issuer_state.next_due_at is not None and issuer_state.next_due_at <= cutoff:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.UNKNOWN),
                    AdapterReadiness.NOT_READY, AdapterReadinessReason.ISSUER_ACQUISITION_STALE,
                )
            events = self._repository.recent_sec_events_by_issuer(
                resolution.identity.issuer_id, limit=_MAX_FACTS, fact_cutoff=cutoff,
            )
            filing = self._select(events, cutoff)
            if filing is None:
                return SecCatalystAdapterEvaluation(
                    self._negative(normalized, CatalystStatus.FALSE), AdapterReadiness.READY
                )
            event, form, accession, document, cik = filing
            evidence = CatalystEvidence(
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
            return SecCatalystAdapterEvaluation(evidence, AdapterReadiness.READY)
        except _UnverifiedFactError:
            return SecCatalystAdapterEvaluation(
                self._negative(normalized, CatalystStatus.UNKNOWN),
                AdapterReadiness.NOT_READY, AdapterReadinessReason.UNVERIFIED_FACT,
            )
        except _MalformedFactError:
            return SecCatalystAdapterEvaluation(
                self._negative(normalized, CatalystStatus.UNKNOWN),
                AdapterReadiness.NOT_READY, AdapterReadinessReason.MALFORMED_FACT,
            )
        except (KeyError, TypeError, ValueError):
            return SecCatalystAdapterEvaluation(
                self._negative(normalized, CatalystStatus.UNKNOWN),
                AdapterReadiness.NOT_READY, AdapterReadinessReason.MALFORMED_FACT,
            )
        except Exception:
            return SecCatalystAdapterEvaluation(
                self._negative(normalized, CatalystStatus.UNKNOWN),
                AdapterReadiness.ERROR, AdapterReadinessReason.REPOSITORY_ERROR,
            )

    def _select(self, events: tuple[Any, ...], cutoff: datetime) -> tuple[Any, str, str, str, int] | None:
        candidates: list[tuple[Any, str, str, str, int]] = []
        for event in events:
            try:
                if event.source != _SEC_SOURCE or event.event_type.value != "SEC_FILING":
                    continue
                if event.verified is not True:
                    raise _UnverifiedFactError("unverified SEC fact")
                form = " ".join(event.event_subtype.strip().upper().split())
                if form not in _SUPPORTED_FORMS and not re.fullmatch(r"424B[A-Z0-9-]*(?:/A)?", form):
                    continue
                metadata = event.metadata
                accession = normalize_accession(str(event.source_id or metadata["accession_number"]))
                filing_date = date.fromisoformat(str(metadata["filing_date"]).strip())
                document = str(metadata["primary_document"]).strip()
                if not accession or not _DOCUMENT.fullmatch(document) or ".." in document:
                    raise _MalformedFactError("SEC fact metadata is malformed")
                if filing_date < (cutoff - timedelta(days=self._freshness_days)).date() or filing_date > cutoff.date():
                    continue
                if event.published_at > cutoff or event.observed_at > cutoff:
                    continue
                issuer_text = str(event.issuer_id or "")
                match = re.fullmatch(r"SEC_CIK:(\d{1,})", issuer_text)
                if match is None or sec_issuer_id(int(match.group(1))) != issuer_text:
                    raise _MalformedFactError("SEC issuer metadata is malformed")
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


__all__ = [
    "AdapterReadiness",
    "AdapterReadinessReason",
    "SecCatalystAdapterEvaluation",
    "SecSymbolIntelligenceCatalystAdapter",
]

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from app.momentum_scanner.models import CatalystStatus, CatalystType


@dataclass(frozen=True, slots=True)
class CatalystEvidence:
    """One immutable provider observation about a possible catalyst event."""

    symbol: str
    catalyst_type: CatalystType
    status: CatalystStatus
    headline: str | None = None
    source: str = "UNKNOWN"
    published_at: datetime | None = None
    source_url: str | None = None
    provider_event_id: str | None = None
    canonical_event_id: str | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        source = self.source.strip()
        if not symbol:
            raise ValueError("catalyst evidence symbol is required")
        if not source:
            raise ValueError("catalyst evidence source is required")
        if not isinstance(self.catalyst_type, CatalystType):
            raise TypeError("catalyst_type must be CatalystType")
        if not isinstance(self.status, CatalystStatus):
            raise TypeError("status must be CatalystStatus")
        published_at = self.published_at
        if published_at is not None:
            if published_at.tzinfo is None or published_at.utcoffset() is None:
                raise ValueError("published_at must be timezone-aware")
            published_at = published_at.astimezone(UTC)

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "headline", _optional_text(self.headline))
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "source_url", _optional_text(self.source_url))
        object.__setattr__(
            self,
            "provider_event_id",
            _optional_text(self.provider_event_id),
        )
        object.__setattr__(
            self,
            "canonical_event_id",
            _optional_text(self.canonical_event_id),
        )

    @property
    def catalyst(self) -> CatalystType:
        """Compatibility alias for scanner-facing catalyst terminology."""

        return self.catalyst_type

    @property
    def event_identity(self) -> str:
        """Stable cross-source event identity, independent of Python hashing."""

        if self.canonical_event_id is not None:
            components = (
                "canonical",
                self.symbol,
                self.catalyst_type.value,
                _identity_text(self.canonical_event_id),
            )
        else:
            published_date = (
                self.published_at.date().isoformat()
                if self.published_at is not None
                else ""
            )
            headline = _identity_text(self.headline or "")
            if published_date or headline:
                components = (
                    "derived",
                    self.symbol,
                    self.catalyst_type.value,
                    published_date,
                    headline,
                )
            else:
                components = (
                    "provider",
                    self.symbol,
                    self.catalyst_type.value,
                    _identity_text(self.source),
                    _identity_text(self.provider_event_id or ""),
                )
        return "catalyst-event-" + _stable_digest(components)

    @property
    def evidence_identity(self) -> str:
        """Stable identity for this source-level corroborating record."""

        components = (
            self.event_identity,
            self.status.value,
            _identity_text(self.source),
            _identity_text(self.headline or ""),
            self.published_at.isoformat() if self.published_at is not None else "",
            self.source_url or "",
            self.provider_event_id or "",
        )
        return "catalyst-evidence-" + _stable_digest(components)

    def as_scanner_fields(
        self,
    ) -> tuple[CatalystType, str | None, CatalystStatus]:
        """Return fields in the order used by existing scanner records."""

        return self.catalyst_type, self.headline, self.status


@dataclass(frozen=True, slots=True)
class CatalystEvent:
    """One deduplicated event with all distinct corroborating evidence."""

    identity: str
    symbol: str
    catalyst_type: CatalystType
    evidence: tuple[CatalystEvidence, ...]

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(
            sorted({item.source for item in self.evidence}, key=str.casefold)
        )


@dataclass(frozen=True, slots=True)
class CatalystAggregationResult:
    """Deterministic selection plus retained source-level evidence."""

    selected: CatalystEvidence
    events: tuple[CatalystEvent, ...]
    evidence: tuple[CatalystEvidence, ...]


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _identity_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _stable_digest(components: tuple[str, ...]) -> str:
    payload = "\x1f".join(components).encode("utf-8")
    return sha256(payload).hexdigest()


__all__ = [
    "CatalystAggregationResult",
    "CatalystEvent",
    "CatalystEvidence",
]

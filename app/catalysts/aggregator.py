from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Callable

from app.catalysts.models import (
    CatalystAggregationResult,
    CatalystEvent,
    CatalystEvidence,
)
from app.catalysts.provider import CatalystProvider
from app.catalysts.policy import (
    DEFAULT_CATALYST_PRIORITY_POLICY,
    CatalystPriorityPolicy,
)
from app.momentum_scanner.models import CatalystStatus, CatalystType


class CatalystAggregator:
    """Collect, deduplicate, and resolve provider evidence deterministically.

    Provider exceptions are deliberately isolated as UNAVAILABLE evidence. This
    differs from the legacy dependency-setup edge case so one broken source
    cannot prevent another source's valid catalyst from reaching the scanner.
    """

    def __init__(
        self,
        providers: Iterable[CatalystProvider],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        priority_policy: CatalystPriorityPolicy = DEFAULT_CATALYST_PRIORITY_POLICY,
    ) -> None:
        self._providers = tuple(providers)
        if not self._providers:
            raise ValueError("at least one catalyst provider is required")
        if not isinstance(priority_policy, CatalystPriorityPolicy):
            raise TypeError("priority_policy must be CatalystPriorityPolicy")
        self._clock = clock
        self._priority_policy = priority_policy

    @property
    def provider_names(self) -> tuple[str, ...]:
        names = (_provider_name(item) for item in self._providers)
        return tuple(sorted(names, key=str.casefold))

    def aggregate_result(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystAggregationResult:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("symbol is required")
        effective_as_of = as_of if as_of is not None else self._clock()

        collected = tuple(
            self._collect(provider, normalized, effective_as_of)
            for provider in self._providers
        )
        evidence = _deduplicate_evidence(collected)
        events = _catalyst_events(evidence, self._priority_policy)
        selected = (
            _select_true(events)
            if events
            else _select_non_positive(evidence, normalized)
        )
        return CatalystAggregationResult(
            selected=selected,
            events=events,
            evidence=evidence,
        )

    def get_evidence(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        return self.aggregate_result(symbol, as_of).selected

    def aggregate(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        """Compatibility alias returning the scanner-selected evidence."""

        return self.get_evidence(symbol, as_of)

    @staticmethod
    def _collect(
        provider: CatalystProvider,
        symbol: str,
        as_of: datetime,
    ) -> CatalystEvidence:
        name = _provider_name(provider)
        try:
            evidence = provider.get_evidence(symbol, as_of)
            if not isinstance(evidence, CatalystEvidence):
                raise TypeError("provider returned invalid catalyst evidence")
            if evidence.symbol != symbol:
                raise ValueError("provider returned evidence for another symbol")
            return evidence
        except Exception:
            return CatalystEvidence(
                symbol=symbol,
                catalyst_type=CatalystType.NONE,
                status=CatalystStatus.UNAVAILABLE,
                source=name,
            )


def _deduplicate_evidence(
    evidence: Iterable[CatalystEvidence],
) -> tuple[CatalystEvidence, ...]:
    unique: dict[str, CatalystEvidence] = {}
    for item in evidence:
        existing = unique.get(item.evidence_identity)
        if (
            existing is None
            or _evidence_sort_key(item) < _evidence_sort_key(existing)
        ):
            unique[item.evidence_identity] = item
    return tuple(sorted(unique.values(), key=_evidence_sort_key))


def _catalyst_events(
    evidence: tuple[CatalystEvidence, ...],
    priority_policy: CatalystPriorityPolicy,
) -> tuple[CatalystEvent, ...]:
    grouped: dict[str, list[CatalystEvidence]] = {}
    for item in evidence:
        if item.status is CatalystStatus.TRUE:
            grouped.setdefault(item.event_identity, []).append(item)

    events = tuple(
        CatalystEvent(
            identity=identity,
            symbol=items[0].symbol,
            catalyst_type=items[0].catalyst_type,
            evidence=tuple(sorted(items, key=_evidence_sort_key)),
        )
        for identity, items in grouped.items()
    )
    return tuple(
        sorted(
            events,
            key=lambda event: _event_sort_key(event, priority_policy),
        )
    )


def _select_true(events: tuple[CatalystEvent, ...]) -> CatalystEvidence:
    return events[0].evidence[0]


def _select_non_positive(
    evidence: tuple[CatalystEvidence, ...],
    symbol: str,
) -> CatalystEvidence:
    for status in (
        CatalystStatus.UNKNOWN,
        CatalystStatus.UNAVAILABLE,
        CatalystStatus.FALSE,
    ):
        matching = tuple(item for item in evidence if item.status is status)
        if matching:
            selected = min(matching, key=_evidence_sort_key)
            return CatalystEvidence(
                symbol=symbol,
                catalyst_type=CatalystType.NONE,
                status=status,
                source=selected.source,
            )
    return CatalystEvidence(
        symbol=symbol,
        catalyst_type=CatalystType.NONE,
        status=CatalystStatus.UNKNOWN,
        source="CATALYST_AGGREGATOR",
    )


def _event_sort_key(
    event: CatalystEvent,
    priority_policy: CatalystPriorityPolicy,
) -> tuple[int, int, str]:
    published_at = max(
        (
            item.published_at
            for item in event.evidence
            if item.published_at is not None
        ),
        default=None,
    )
    return (
        -priority_policy.priority(event.catalyst_type),
        -_datetime_rank(published_at),
        event.identity,
    )


def _evidence_sort_key(item: CatalystEvidence) -> tuple[object, ...]:
    return (
        item.event_identity,
        -_datetime_rank(item.published_at),
        item.source.casefold(),
        (item.provider_event_id or "").casefold(),
        (item.headline or "").casefold(),
        item.source_url or "",
        item.evidence_identity,
    )


def _datetime_rank(value: datetime | None) -> int:
    if value is None:
        return -1
    utc = value.astimezone(UTC)
    return (
        utc.toordinal() * 86_400_000_000
        + ((utc.hour * 60 + utc.minute) * 60 + utc.second) * 1_000_000
        + utc.microsecond
    )


def _provider_name(provider: CatalystProvider) -> str:
    value = str(getattr(provider, "name", "")).strip()
    return value or type(provider).__name__


__all__ = ["CatalystAggregator"]

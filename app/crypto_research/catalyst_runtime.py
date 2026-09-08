"""Bounded, failure-isolated acquisition of normalized crypto catalyst evidence."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from typing import Protocol

from .catalysts import CryptoCatalystAggregator, CryptoCatalystEvidence, CryptoCatalystProvider, CryptoPairIdentity
from .catalyst_snapshot import (
    CryptoCatalystDecisionSnapshot,
    CryptoCatalystProviderAvailability,
    CryptoCatalystProviderAvailabilityRecord,
    CryptoCatalystProviderTier,
    build_crypto_catalyst_snapshot,
)


DEFAULT_CADENCES: dict[str, float] = {
    "SEC_EDGAR": 3_600.0,
    "FEDERAL_REGISTER": 7_200.0,
    "STATUSPAGE": 300.0,
    "BYBIT": 900.0,
}
PROVIDER_TIERS: dict[str, CryptoCatalystProviderTier] = {
    "SEC_EDGAR": CryptoCatalystProviderTier.OFFICIAL_REGULATORY,
    "FEDERAL_REGISTER": CryptoCatalystProviderTier.OFFICIAL_REGULATORY,
    "STATUSPAGE": CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY,
    "BYBIT": CryptoCatalystProviderTier.OFFICIAL_FIRST_PARTY,
}


class CatalystProvider(Protocol):
    provider_id: str

    def collect(self, as_of: datetime) -> Iterable[CryptoCatalystEvidence]: ...


DEFAULT_INITIAL_LOOKBACK_SECONDS: dict[str, float] = {
    "SEC_EDGAR": 30.0 * 86_400.0,
    "FEDERAL_REGISTER": 7.0 * 86_400.0,
    "STATUSPAGE": 7.0 * 86_400.0,
}


class FetchSinceCatalystProviderAdapter:
    """Adapt an existing cutoff-aware ``fetch_since`` provider to ``collect``."""

    def __init__(
        self,
        provider: object,
        *,
        initial_lookback_seconds: float | None = None,
    ) -> None:
        provider_id = str(getattr(provider, "provider_id", "")).strip()
        fetch_since = getattr(provider, "fetch_since", None)
        if not provider_id or not callable(fetch_since):
            raise TypeError("provider must expose provider_id and fetch_since")
        lookback = (
            DEFAULT_INITIAL_LOOKBACK_SECONDS.get(provider_id, 7.0 * 86_400.0)
            if initial_lookback_seconds is None
            else float(initial_lookback_seconds)
        )
        if lookback <= 0:
            raise ValueError("initial lookback must be positive")
        self.provider_id = provider_id
        self.provider = provider
        self.initial_lookback = timedelta(seconds=lookback)
        self._watermark: datetime | None = None

    @property
    def watermark(self) -> datetime | None:
        return self._watermark

    def collect(self, as_of: datetime) -> tuple[CryptoCatalystEvidence, ...]:
        observed = as_of.astimezone(UTC)
        since = self._watermark or (observed - self.initial_lookback)
        evidence = tuple(self.provider.fetch_since(since, observed_at=observed))
        self._watermark = observed
        return evidence


def adapt_catalyst_provider(provider: object) -> CatalystProvider:
    """Preserve native ``collect`` providers and bridge ``fetch_since`` providers."""

    if callable(getattr(provider, "collect", None)):
        return provider  # type: ignore[return-value]
    return FetchSinceCatalystProviderAdapter(provider)


def set_catalyst_provider_enabled(provider: object, enabled: bool) -> object:
    """Align an injected provider policy with the outer provider enable flag."""

    policy = getattr(provider, "policy", None)
    if policy is None or not hasattr(policy, "enabled"):
        return provider
    try:
        provider.policy = replace(policy, enabled=bool(enabled))
    except (AttributeError, TypeError, ValueError):
        # Providers without replaceable policies retain their own policy semantics.
        pass
    return provider


@dataclass(frozen=True, slots=True)
class CryptoCatalystAcquisitionMetrics:
    enabled: bool
    scheduler_cycles: int
    providers_enabled: int
    providers_available: int
    providers_failed: int
    providers_blocked: int
    requests_attempted: int
    requests_succeeded: int
    requests_failed: int
    evidence_received: int
    evidence_admitted: int
    duplicate_evidence: int
    next_due_seconds: int | None


class CryptoCatalystAcquisitionRuntime:
    """One bounded scheduler owning provider cadence and the canonical aggregator."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        providers: Sequence[CatalystProvider] = (),
        aggregator: CryptoCatalystAggregator | None = None,
        cadences: Mapping[str, float] | None = None,
        provider_enabled: Mapping[str, bool] | None = None,
        scheduler_tick_seconds: float = 5.0,
        availability_history: int = 64,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        if scheduler_tick_seconds <= 0 or availability_history <= 0:
            raise ValueError("acquisition bounds must be positive")
        supplied = tuple(providers)
        if len({item.provider_id for item in supplied}) != len(supplied):
            raise ValueError("provider IDs must be unique")
        values = dict(DEFAULT_CADENCES)
        values.update(cadences or {})
        if any(float(value) <= 0 for value in values.values()):
            raise ValueError("provider cadences must be positive")
        self.enabled = bool(enabled)
        self.providers = supplied
        self.aggregator = aggregator or CryptoCatalystAggregator()
        self.cadences = values
        self.provider_enabled = dict(provider_enabled or {})
        self.scheduler_tick_seconds = scheduler_tick_seconds
        self._clock = clock
        self._lock = Lock()
        self._stop = Event()
        self._worker: Thread | None = None
        self._accepting = False
        self._next_due: dict[str, datetime] = {}
        self._availability: dict[str, deque[CryptoCatalystProviderAvailabilityRecord]] = {
            item.provider_id: deque(maxlen=availability_history) for item in supplied
        }
        self._cycles = self._attempted = self._succeeded = self._failed = 0
        self._received = self._admitted = self._duplicates = 0

    def start(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._worker is not None:
                return False
            now = self._clock()
            for index, provider in enumerate(sorted(self.providers, key=lambda item: item.provider_id)):
                if self.provider_enabled.get(provider.provider_id, True) is False:
                    continue
                self._next_due[provider.provider_id] = now + timedelta(seconds=index * 30)
            self._accepting = True
            self._stop.clear()
            self._worker = Thread(target=self._run, name="atlas-crypto-catalyst-acquisition", daemon=True)
            self._worker.start()
            return True

    def run_once(self, *, now: datetime | None = None, force: bool = False) -> int:
        if not self.enabled or not self._accepting:
            return 0
        observed = (now or self._clock()).astimezone(UTC)
        self._cycles += 1
        admitted = 0
        for provider in sorted(self.providers, key=lambda item: item.provider_id):
            if self.provider_enabled.get(provider.provider_id, True) is False:
                continue
            due = self._next_due.get(provider.provider_id)
            if not force and due is not None and observed < due:
                continue
            provider_id = provider.provider_id
            self._next_due[provider_id] = observed + timedelta(seconds=self.cadences.get(provider_id, 900.0))
            self._attempted += 1
            try:
                evidence = tuple(provider.collect(observed))
                self._received += len(evidence)
                before = self.aggregator.metrics().crypto_catalyst_duplicates_suppressed
                aggregation = self.aggregator.aggregate(evidence, decision_cutoff=observed)
                after = aggregation.metrics.crypto_catalyst_duplicates_suppressed
                admitted += max(0, len(evidence) - aggregation.rejected_future)
                self._admitted += max(0, len(evidence) - aggregation.rejected_future)
                self._duplicates += max(0, after - before)
                self._succeeded += 1
                self._record_availability(provider_id, CryptoCatalystProviderAvailability.AVAILABLE, observed)
            except Exception as exc:
                self._failed += 1
                state = CryptoCatalystProviderAvailability.BLOCKED if _is_blocked(exc) else CryptoCatalystProviderAvailability.FAILED
                self._record_availability(provider_id, state, observed, _failure_category(exc))
        return admitted

    def snapshot_at(self, pair: CryptoPairIdentity, cutoff: datetime) -> CryptoCatalystDecisionSnapshot:
        boundary = cutoff.astimezone(UTC)
        availability = []
        for provider in sorted(self.providers, key=lambda item: item.provider_id):
            history = self._availability.get(provider.provider_id, ())
            prior = [item for item in history if item.evidence_cutoff is not None and item.evidence_cutoff <= boundary]
            if prior:
                availability.append(prior[-1])
            else:
                availability.append(CryptoCatalystProviderAvailabilityRecord(
                    provider_id=provider.provider_id,
                    availability_state=CryptoCatalystProviderAvailability.NOT_QUERIED,
                    source_tier=PROVIDER_TIERS.get(provider.provider_id, CryptoCatalystProviderTier.UNVERIFIED),
                    evidence_cutoff=boundary,
                ))
        return build_crypto_catalyst_snapshot(
            pair=pair,
            decision_cutoff=boundary,
            evidence=self.aggregator.evidence_at(boundary),
            provider_tiers=PROVIDER_TIERS,
            provider_availability=availability,
        )

    def close(self, *, timeout_seconds: float = 2.0) -> bool:
        self._accepting = False
        self._stop.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout_seconds)
        return worker is None or not worker.is_alive()

    def metrics(self) -> CryptoCatalystAcquisitionMetrics:
        available = blocked = failed = 0
        for history in self._availability.values():
            if not history:
                continue
            state = history[-1].availability_state
            available += state is CryptoCatalystProviderAvailability.AVAILABLE
            blocked += state is CryptoCatalystProviderAvailability.BLOCKED
            failed += state is CryptoCatalystProviderAvailability.FAILED
        due = [max(0, int((value - self._clock()).total_seconds())) for value in self._next_due.values()]
        return CryptoCatalystAcquisitionMetrics(
            self.enabled, self._cycles, sum(self.provider_enabled.get(item.provider_id, True) for item in self.providers), available, failed, blocked,
            self._attempted, self._succeeded, self._failed, self._received, self._admitted,
            self._duplicates, min(due) if due else None,
        )

    def memory_metrics(self) -> dict[str, int]:
        metrics = self.metrics()
        return {
            "crypto_catalyst_scheduler_cycles": metrics.scheduler_cycles,
            "crypto_catalyst_requests_attempted": metrics.requests_attempted,
            "crypto_catalyst_requests_succeeded": metrics.requests_succeeded,
            "crypto_catalyst_requests_failed": metrics.requests_failed,
            "crypto_catalyst_evidence_received": metrics.evidence_received,
            "crypto_catalyst_evidence_admitted": metrics.evidence_admitted,
            "crypto_catalyst_duplicate_evidence": metrics.duplicate_evidence,
        }

    def _record_availability(self, provider_id: str, state: CryptoCatalystProviderAvailability, observed: datetime, failure: str | None = None) -> None:
        self._availability.setdefault(provider_id, deque(maxlen=64)).append(
            CryptoCatalystProviderAvailabilityRecord(
                provider_id=provider_id,
                availability_state=state,
                source_tier=PROVIDER_TIERS.get(provider_id, CryptoCatalystProviderTier.UNVERIFIED),
                last_success_at=observed if state is CryptoCatalystProviderAvailability.AVAILABLE else None,
                last_attempt_at=observed,
                failure_category=failure,
                evidence_cutoff=observed,
            )
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.scheduler_tick_seconds)


def _is_blocked(exc: Exception) -> bool:
    text = str(exc).casefold()
    return any(token in text for token in ("cloudfront", "geographic", "geo-block", "blocked"))


def _failure_category(exc: Exception) -> str:
    return "BLOCKED_BY_PROVIDER_GEOGRAPHIC_POLICY" if _is_blocked(exc) else type(exc).__name__.upper()


__all__ = [
    "DEFAULT_CADENCES",
    "DEFAULT_INITIAL_LOOKBACK_SECONDS",
    "CryptoCatalystAcquisitionMetrics",
    "CryptoCatalystAcquisitionRuntime",
    "FetchSinceCatalystProviderAdapter",
    "adapt_catalyst_provider",
    "set_catalyst_provider_enabled",
]

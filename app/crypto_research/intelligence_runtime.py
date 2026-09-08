"""Bounded research-only orchestration for crypto intelligence stages."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Callable, Iterable, Protocol

from .catalyst_collection import CryptoCatalystCollectionSink
from .catalyst_snapshot import (
    CryptoCatalystDecisionSnapshot,
    CryptoCatalystProviderAvailability,
    CryptoCatalystProviderAvailabilityRecord,
    CryptoCatalystProviderTier,
    build_crypto_catalyst_snapshot,
)
from .catalysts import CryptoPairIdentity
from .outcomes import SUPPORTED_HORIZONS_SECONDS, CryptoOutcomeDecision, CryptoOutcomeTracker, CryptoResearchOutcome
from .strategies import CryptoDiscoveryContext, CryptoDiscoveryEngine
from .evidence import CryptoCompletedBar


class CatalystEvidenceView(Protocol):
    """Read-only, cutoff-aware normalized catalyst source."""

    def snapshot_at(self, pair: CryptoPairIdentity, cutoff: datetime) -> CryptoCatalystDecisionSnapshot:
        ...


@dataclass(frozen=True, slots=True)
class CryptoIntelligenceMetrics:
    enabled: bool
    contexts_seen: int
    symbols_evaluated: int
    detections: int
    decisions_registered: int
    decision_duplicates: int
    active_decisions: int
    active_decisions_high_water: int
    snapshots_built: int
    snapshot_failures: int
    outcome_updates: int
    outcomes_completed: int
    outcome_failures: int
    collection_capture_failures: int
    linkage_rejections: int


@dataclass(frozen=True, slots=True)
class _Link:
    decision: CryptoOutcomeDecision
    snapshot: CryptoCatalystDecisionSnapshot


class CryptoIntelligenceResearchRuntime:
    """Coordinates existing Phase C/D/G/G2/G3A contracts without market ownership."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        discovery: CryptoDiscoveryEngine | None = None,
        outcomes: CryptoOutcomeTracker | None = None,
        catalyst_view: CatalystEvidenceView | None = None,
        collection: CryptoCatalystCollectionSink | None = None,
        maximum_active_decisions: int = 4096,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if maximum_active_decisions <= 0:
            raise ValueError("maximum_active_decisions must be positive")
        self.enabled = bool(enabled)
        self.discovery = discovery or CryptoDiscoveryEngine()
        self.outcomes = outcomes or CryptoOutcomeTracker(maximum_active_episodes=maximum_active_decisions)
        self.catalyst_view = catalyst_view
        self.collection = collection
        self.maximum_active_decisions = maximum_active_decisions
        self._clock = clock
        self._links: OrderedDict[str, _Link] = OrderedDict()
        self._accepting = False
        self._contexts_seen = self._symbols_evaluated = self._detections = 0
        self._decisions_registered = self._decision_duplicates = 0
        self._active_high_water = self._snapshots_built = self._snapshot_failures = 0
        self._outcome_updates = self._outcomes_completed = self._outcome_failures = 0
        self._collection_capture_failures = self._linkage_rejections = 0

    def start(self) -> bool:
        if not self.enabled:
            return False
        self._accepting = True
        if self.collection is not None:
            try:
                self.collection.start()
            except Exception:
                self._collection_capture_failures += 1
        return True

    def submit_context(self, context: CryptoDiscoveryContext) -> tuple[CryptoOutcomeDecision, ...]:
        """Consume one immutable Phase B context and freeze accepted decisions."""
        if not self.enabled or not self._accepting:
            return ()
        self._contexts_seen += 1
        self._symbols_evaluated += 1
        try:
            result = self.discovery.evaluate((context,))
        except Exception:
            self._snapshot_failures += 1
            return ()
        self._detections += len(result.detections)
        accepted: list[CryptoOutcomeDecision] = []
        for opportunity in result.opportunities:
            memberships = opportunity.memberships
            for detection in opportunity.detections:
                if detection.risk_geometry is None:
                    continue
                decision = CryptoOutcomeDecision.from_detection(
                    detection,
                    decision_timestamp=context.decision_cutoff,
                    regime_window=context.evidence.regime.label.value,
                    btc_relative_strength=context.evidence.btc_relative_strength.excess_return_percent,
                    eth_relative_strength=context.evidence.eth_relative_strength.excess_return_percent,
                    btc_correlation=context.evidence.btc_correlation.correlation,
                    eth_correlation=context.evidence.eth_correlation.correlation,
                    breadth=context.evidence.breadth.advancing_percent,
                    dispersion=context.evidence.dispersion.standard_deviation,
                    memberships=memberships,
                )
                key = decision.identity.deterministic_id
                if key in self._links:
                    self._decision_duplicates += 1
                    continue
                if len(self._links) >= self.maximum_active_decisions:
                    self._linkage_rejections += 1
                    continue
                try:
                    snapshot = self._snapshot(context)
                except Exception:
                    self._snapshot_failures += 1
                    continue
                if not self.outcomes.register(decision):
                    self._decision_duplicates += 1
                    continue
                self._links[key] = _Link(decision, snapshot)
                self._active_high_water = max(self._active_high_water, len(self._links))
                self._decisions_registered += 1
                self._snapshots_built += 1
                if self.collection is not None:
                    try:
                        if not self.collection.capture_decision(decision, snapshot):
                            self._collection_capture_failures += 1
                    except Exception:
                        self._collection_capture_failures += 1
                accepted.append(decision)
        return tuple(accepted)

    def update_outcomes(
        self,
        decision_id: str,
        bars: Iterable[CryptoCompletedBar],
        *,
        btc_bars: Iterable[CryptoCompletedBar] = (),
        eth_bars: Iterable[CryptoCompletedBar] = (),
    ) -> tuple[CryptoResearchOutcome, ...]:
        """Feed existing Phase D tracking and forward newly completed horizons."""
        if not self.enabled or not self._accepting:
            return ()
        link = self._links.get(decision_id)
        if link is None:
            self._outcome_failures += 1
            return ()
        try:
            outcomes = self.outcomes.update(decision_id, tuple(bars), btc_bars=tuple(btc_bars), eth_bars=tuple(eth_bars))
        except Exception:
            self._outcome_failures += 1
            return ()
        self._outcome_updates += 1
        supported_outcomes: list[CryptoResearchOutcome] = []
        for outcome in outcomes:
            if outcome.horizon_seconds not in SUPPORTED_HORIZONS_SECONDS:
                continue
            supported_outcomes.append(outcome)
            self._outcomes_completed += 1
            if self.collection is not None:
                try:
                    if not self.collection.record_outcome(link.decision, outcome):
                        self._collection_capture_failures += 1
                except Exception:
                    self._collection_capture_failures += 1
        if decision_id not in getattr(self.outcomes, "_decisions", {}):
            self._links.pop(decision_id, None)
        return tuple(supported_outcomes)

    def _snapshot(self, context: CryptoDiscoveryContext) -> CryptoCatalystDecisionSnapshot:
        pair = CryptoPairIdentity(context.canonical_symbol, context.provider_symbol)
        if self.catalyst_view is not None:
            snapshot = self.catalyst_view.snapshot_at(pair, context.decision_cutoff)
            if snapshot.decision_cutoff != context.decision_cutoff or snapshot.canonical_pair != context.canonical_symbol:
                raise ValueError("catalyst view returned a mismatched snapshot")
            return snapshot
        unavailable = CryptoCatalystProviderAvailabilityRecord(
            provider_id="catalyst-acquisition",
            availability_state=CryptoCatalystProviderAvailability.FAILED,
            source_tier=CryptoCatalystProviderTier.UNAVAILABLE,
            failure_category="CATALYST_ACQUISITION_NOT_COMPOSED",
            evidence_cutoff=context.decision_cutoff,
        )
        return build_crypto_catalyst_snapshot(
            pair=pair,
            decision_cutoff=context.decision_cutoff,
            evidence=(),
            provider_availability=(unavailable,),
        )

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        self._accepting = False
        if self.collection is None:
            return True
        try:
            return self.collection.close(timeout_seconds=timeout_seconds)
        except Exception:
            self._collection_capture_failures += 1
            return False

    def metrics(self) -> CryptoIntelligenceMetrics:
        return CryptoIntelligenceMetrics(
            self.enabled, self._contexts_seen, self._symbols_evaluated, self._detections,
            self._decisions_registered, self._decision_duplicates, len(self._links),
            self._active_high_water, self._snapshots_built, self._snapshot_failures,
            self._outcome_updates, self._outcomes_completed, self._outcome_failures,
            self._collection_capture_failures, self._linkage_rejections,
        )

    def memory_metrics(self) -> dict[str, int]:
        metrics = self.metrics()
        return {key: int(value) for key, value in asdict(metrics).items() if isinstance(value, (int, bool))}


__all__ = ["CatalystEvidenceView", "CryptoIntelligenceMetrics", "CryptoIntelligenceResearchRuntime"]

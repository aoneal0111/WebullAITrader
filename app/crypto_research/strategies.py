"""Research-only crypto multi-strategy discovery over Phase B evidence.

This module owns no market-data acquisition, selection, sizing, broker, PAPER,
LIVE, or GUI authority.  It consumes an already-built ``CryptoEvidenceContext``
and emits bounded shared-core research contracts.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Callable

from app.assets import AssetType
from app.research_core import (
    AssetResearchIdentity,
    DetectorRegistry,
    EvidenceAvailability,
    EvidenceProvenance,
    PerUnitRiskGeometry,
    ResearchDetection,
    ResearchDirection,
    ResearchOpportunity,
    ScoreComponent,
    semantic_digest,
)

from .evidence import (
    CryptoBarInterval,
    CryptoCompletedBar,
    CryptoEvidenceContext,
    CryptoRegimeLabel,
)


MAX_SYMBOLS = 256
MAX_DETECTIONS = 4096
MAX_SIGNATURES = 16384
MAX_EPISODES = 8192


class CryptoStrategyState(StrEnum):
    ACTIVE = "ACTIVE"
    UNAVAILABLE_EVIDENCE = "UNAVAILABLE_EVIDENCE"
    FUTURE = "FUTURE"


class CryptoStrategyFamily(StrEnum):
    PULLBACK = "PULLBACK"
    CONTINUATION = "CONTINUATION"
    BREAKOUT = "BREAKOUT"
    MOMENTUM = "MOMENTUM"
    RELATIVE_STRENGTH = "RELATIVE_STRENGTH"
    BREADTH = "BREADTH"
    CATALYST = "CATALYST"
    MICROSTRUCTURE = "MICROSTRUCTURE"
    EQUITY_SESSION = "EQUITY_SESSION"


@dataclass(frozen=True, slots=True)
class CryptoStrategyDefinition:
    strategy_id: str
    version: str
    family: CryptoStrategyFamily
    state: CryptoStrategyState
    required_evidence: tuple[str, ...]
    research_only: bool = True
    selection_authorized: bool = False
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.version.strip():
            raise ValueError("crypto strategy identity is required")
        if not self.required_evidence:
            raise ValueError("crypto strategy evidence requirements are required")
        if not self.research_only or self.selection_authorized or self.execution_authorized:
            raise ValueError("crypto strategies are research-only")


@dataclass(frozen=True, slots=True)
class CryptoStrategyDetection:
    definition: CryptoStrategyDefinition
    detection: ResearchDetection
    score_components: tuple[ScoreComponent, ...]
    risk_geometry: PerUnitRiskGeometry | None
    research_targets_r: tuple[Decimal, Decimal, Decimal] | None = None

    @property
    def identity(self) -> AssetResearchIdentity:
        return self.detection.identity

    @property
    def strategy_id(self) -> str:
        return self.definition.strategy_id


@dataclass(frozen=True, slots=True)
class CryptoResearchOpportunity:
    opportunity: ResearchOpportunity
    memberships: tuple[str, ...]
    detections: tuple[CryptoStrategyDetection, ...]
    score_components: tuple[ScoreComponent, ...]
    rank_score: Decimal | None
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only or not self.opportunity.research_only:
            raise ValueError("crypto opportunities are research-only")
        if not self.memberships or len(self.memberships) != len(set(self.memberships)):
            raise ValueError("opportunity memberships must be unique")


@dataclass(frozen=True, slots=True)
class CryptoDiscoveryMetrics:
    crypto_strategy_registry_count: int
    crypto_strategy_active_count: int
    crypto_strategy_unavailable_count: int
    crypto_strategy_detections: int
    crypto_strategy_opportunity_count: int
    crypto_strategy_episode_count: int
    crypto_strategy_duplicates_suppressed: int
    crypto_strategy_detector_failures: int
    crypto_strategy_symbols: int


@dataclass(frozen=True, slots=True)
class CryptoDiscoveryResult:
    detections: tuple[CryptoStrategyDetection, ...]
    opportunities: tuple[CryptoResearchOpportunity, ...]
    metrics: CryptoDiscoveryMetrics
    research_only: bool = True

    def __post_init__(self) -> None:
        if not self.research_only:
            raise ValueError("discovery result must remain research-only")


@dataclass(frozen=True, slots=True)
class CryptoDiscoveryContext:
    """Asset-specific adapter around the Phase B evidence context."""

    evidence: CryptoEvidenceContext

    @property
    def canonical_symbol(self) -> str:
        return self.evidence.canonical_symbol

    @property
    def provider_symbol(self) -> str:
        return self.evidence.provider_symbol

    @property
    def decision_cutoff(self) -> datetime:
        return self.evidence.decision_cutoff

    @property
    def bars_by_interval(self) -> tuple[tuple[CryptoBarInterval, tuple[CryptoCompletedBar, ...]], ...]:
        return self.evidence.bars_by_interval

    def bars(self, interval: CryptoBarInterval = CryptoBarInterval.M5) -> tuple[CryptoCompletedBar, ...]:
        for candidate, values in self.bars_by_interval:
            if candidate is interval:
                return values
        return ()


def _definition(
    strategy_id: str,
    family: CryptoStrategyFamily,
    state: CryptoStrategyState,
    *required: str,
) -> CryptoStrategyDefinition:
    return CryptoStrategyDefinition(strategy_id, "crypto-phase-c-v1", family, state, tuple(required))


_TAXONOMY = (
    _definition("MICRO_PULLBACK", CryptoStrategyFamily.PULLBACK, CryptoStrategyState.ACTIVE, "M5 completed bars", "structural pullback"),
    _definition("FIRST_PULLBACK", CryptoStrategyFamily.PULLBACK, CryptoStrategyState.FUTURE, "M5 completed bars", "first pullback episode"),
    _definition("HIGHER_LOW_CONTINUATION", CryptoStrategyFamily.CONTINUATION, CryptoStrategyState.ACTIVE, "M5 completed bars", "higher low structure"),
    _definition("SHALLOW_PULLBACK_CONTINUATION", CryptoStrategyFamily.PULLBACK, CryptoStrategyState.FUTURE, "M5 completed bars", "pullback depth classification"),
    _definition("DEEP_PULLBACK_RECLAIM", CryptoStrategyFamily.PULLBACK, CryptoStrategyState.FUTURE, "M5 completed bars", "pullback depth classification"),
    _definition("VOLUME_CONTRACTION_PULLBACK", CryptoStrategyFamily.PULLBACK, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "M5 volume"),
    _definition("MOMENTUM_REACCELERATION", CryptoStrategyFamily.MOMENTUM, CryptoStrategyState.ACTIVE, "M5 completed bars", "price acceleration"),
    _definition("HIGH_OF_DAY_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "rolling high with declared session semantics"),
    _definition("FLAT_TOP_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.FUTURE, "flat-top structure validation"),
    _definition("CONSOLIDATION_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.FUTURE, "consolidation structure validation"),
    _definition("ASCENDING_BASE_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.FUTURE, "ascending-base structure validation"),
    _definition("RANGE_COMPRESSION_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.ACTIVE, "M5 completed bars", "range compression"),
    _definition("BREAKOUT_RETEST_CONTINUATION", CryptoStrategyFamily.CONTINUATION, CryptoStrategyState.ACTIVE, "M5 completed bars", "breakout and retest"),
    _definition("OPENING_RANGE_BREAKOUT", CryptoStrategyFamily.EQUITY_SESSION, CryptoStrategyState.FUTURE, "equity opening range"),
    _definition("PREMARKET_HIGH_BREAKOUT", CryptoStrategyFamily.EQUITY_SESSION, CryptoStrategyState.FUTURE, "equity premarket high"),
    _definition("PREMARKET_CONSOLIDATION_BREAKOUT", CryptoStrategyFamily.EQUITY_SESSION, CryptoStrategyState.FUTURE, "equity premarket consolidation"),
    _definition("OPENING_DRIVE_CONTINUATION", CryptoStrategyFamily.EQUITY_SESSION, CryptoStrategyState.FUTURE, "equity opening drive"),
    _definition("FAILED_BREAKOUT_RECLAIM", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.ACTIVE, "M5 completed bars", "failed breakout reclaim"),
    _definition("HOD_RECLAIM", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "rolling high with declared session semantics"),
    _definition("GAP_AND_GO_CONTINUATION", CryptoStrategyFamily.EQUITY_SESSION, CryptoStrategyState.FUTURE, "equity gap/open semantics"),
    _definition("POST_GAP_RECLAIM", CryptoStrategyFamily.EQUITY_SESSION, CryptoStrategyState.FUTURE, "equity gap semantics"),
    _definition("DIP_AND_RIP", CryptoStrategyFamily.MOMENTUM, CryptoStrategyState.FUTURE, "dip/recovery structure validation"),
    _definition("MOMENTUM_SQUEEZE_EXPANSION", CryptoStrategyFamily.MOMENTUM, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "volume contraction/expansion"),
    _definition("BTC_ETH_RELATIVE_STRENGTH_LEADERSHIP", CryptoStrategyFamily.RELATIVE_STRENGTH, CryptoStrategyState.ACTIVE, "BTC relative strength", "ETH relative strength", "non-risk-off regime"),
    _definition("BREADTH_ALIGNED_TREND_CONTINUATION", CryptoStrategyFamily.BREADTH, CryptoStrategyState.ACTIVE, "trend structure", "BTC/ETH relative strength", "breadth"),
    _definition("REGIME_WINDOW_RANGE_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.FUTURE, "declared regime-window range"),
    _definition("REGIME_WINDOW_HIGH_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.FUTURE, "declared regime-window high"),
    _definition("REGIME_WINDOW_COMPRESSION_BREAKOUT", CryptoStrategyFamily.BREAKOUT, CryptoStrategyState.FUTURE, "declared regime-window compression"),
    _definition("REGIME_WINDOW_DRIVE_CONTINUATION", CryptoStrategyFamily.CONTINUATION, CryptoStrategyState.FUTURE, "declared regime-window drive"),
    _definition("CONTINUOUS_DISPLACEMENT_CONTINUATION", CryptoStrategyFamily.CONTINUATION, CryptoStrategyState.FUTURE, "24/7 displacement episode"),
    _definition("CRYPTO_CATALYST_CONTINUATION", CryptoStrategyFamily.CATALYST, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "canonical crypto catalyst provenance"),
    _definition("LIQUIDITY_SWEEP_RECLAIM", CryptoStrategyFamily.MICROSTRUCTURE, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "multi-level depth"),
    _definition("DEPTH_PRESSURE_CONTINUATION", CryptoStrategyFamily.MICROSTRUCTURE, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "multi-level depth"),
    _definition("ORDER_BOOK_IMBALANCE_CONTINUATION", CryptoStrategyFamily.MICROSTRUCTURE, CryptoStrategyState.UNAVAILABLE_EVIDENCE, "order-book imbalance"),
)


def crypto_strategy_taxonomy() -> tuple[CryptoStrategyDefinition, ...]:
    return _TAXONOMY


def active_crypto_strategies() -> tuple[CryptoStrategyDefinition, ...]:
    return tuple(item for item in _TAXONOMY if item.state is CryptoStrategyState.ACTIVE)


def _bar_series(context: CryptoDiscoveryContext) -> tuple[CryptoCompletedBar, ...]:
    values = context.bars(CryptoBarInterval.M5)
    return values if values else context.bars(CryptoBarInterval.M1)


def _positive(value: Decimal | None) -> bool:
    return value is not None and value > 0


def _returns(bars: tuple[CryptoCompletedBar, ...]) -> tuple[Decimal, ...]:
    return tuple(bars[index].close / bars[index - 1].close - Decimal("1") for index in range(1, len(bars)))


def _geometry(bars: tuple[CryptoCompletedBar, ...]) -> tuple[Decimal, Decimal] | None:
    if len(bars) < 3:
        return None
    entry = bars[-1].close
    stop = min(item.low for item in bars[-4:-1])
    if stop <= 0 or entry <= stop:
        return None
    return entry, stop


def _provenance(context: CryptoDiscoveryContext) -> EvidenceProvenance:
    return context.evidence.regime.provenance


def _attempt(
    definition: CryptoStrategyDefinition,
    context: CryptoDiscoveryContext,
    *,
    reference: Decimal,
    trigger: Decimal,
    stop: Decimal,
    reasons: tuple[str, ...],
    quality: tuple[tuple[str, Decimal], ...],
    observed: tuple[str, ...],
) -> CryptoStrategyDetection:
    provenance = _provenance(context)
    semantic = semantic_digest(definition.strategy_id, context.canonical_symbol, str(reference), str(trigger), str(stop), tuple(sorted(quality)))
    identity = AssetResearchIdentity(
        AssetType.CRYPTO,
        context.canonical_symbol,
        definition.strategy_id,
        definition.version,
        context.decision_cutoff,
        semantic,
        semantic,
    )
    detection = ResearchDetection(
        identity,
        definition.family.value,
        definition.state.value,
        reference,
        trigger,
        stop,
        quality,
        observed,
        (),
        reasons,
        provenance,
    )
    geometry = PerUnitRiskGeometry(reference, stop, reference - stop, ResearchDirection.LONG, reference + 2 * (reference - stop), Decimal("2"))
    components = tuple(
        ScoreComponent(name, value, EvidenceAvailability.AVAILABLE, definition.version, Decimal("1"), provenance)
        for name, value in quality
    )
    risk = reference - stop
    return CryptoStrategyDetection(
        definition, detection, components, geometry,
        (reference + risk, reference + 2 * risk, reference + 3 * risk),
    )


def _bar_detector(definition: CryptoStrategyDefinition, context: CryptoDiscoveryContext) -> CryptoStrategyDetection | None:
    bars = _bar_series(context)
    if len(bars) < 6:
        return None
    geometry = _geometry(bars)
    if geometry is None:
        return None
    entry, stop = geometry
    prior = bars[:-1]
    latest = bars[-1]
    returns = _returns(bars)
    trigger = max(item.high for item in prior[-3:])
    match definition.strategy_id:
        case "MICRO_PULLBACK":
            hit = prior[-2].close <= prior[-3].close and latest.close > prior[-2].close
            reason = "completed-bar pullback reclaimed prior close"
        case "HIGHER_LOW_CONTINUATION":
            hit = min(item.low for item in bars[-3:]) > min(item.low for item in bars[-6:-3]) and latest.close > trigger
            reason = "recent low is higher and continuation cleared local trigger"
        case "MOMENTUM_REACCELERATION":
            hit = len(returns) >= 4 and returns[-1] > 0 and returns[-1] > returns[-2] > returns[-3]
            reason = "positive completed-bar velocity reaccelerated"
        case "RANGE_COMPRESSION_BREAKOUT":
            old_range = sum((item.high - item.low for item in bars[-6:-2]), Decimal("0")) / Decimal("4")
            new_range = bars[-2].high - bars[-2].low
            hit = old_range > 0 and new_range < old_range * Decimal("0.75") and latest.close > bars[-2].high
            reason = "compressed range expanded through prior high"
        case "BREAKOUT_RETEST_CONTINUATION":
            breakout = max(item.high for item in bars[-6:-3])
            hit = bars[-3].close > breakout and bars[-2].low <= breakout and latest.close > breakout
            trigger = breakout
            reason = "breakout level was retested and reclaimed"
        case "FAILED_BREAKOUT_RECLAIM":
            breakout = max(item.high for item in bars[-5:-2])
            hit = bars[-2].high > breakout and bars[-2].close < breakout and latest.close > breakout
            trigger = breakout
            reason = "failed breakout was reclaimed on a completed bar"
        case _:
            return None
    if not hit or entry <= stop:
        return None
    quality = (("price_structure", Decimal("1")), ("regime_alignment", Decimal("1")))
    return _attempt(definition, context, reference=entry, trigger=trigger, stop=stop, reasons=(reason,), quality=quality, observed=("completed_bars", "regime"))


def _relative_strength_detector(definition: CryptoStrategyDefinition, context: CryptoDiscoveryContext) -> CryptoStrategyDetection | None:
    rs_btc = context.evidence.btc_relative_strength.excess_return_percent
    rs_eth = context.evidence.eth_relative_strength.excess_return_percent
    regime = context.evidence.regime.label
    geometry = _geometry(_bar_series(context))
    if not (_positive(rs_btc) and _positive(rs_eth) and geometry and regime not in {CryptoRegimeLabel.BROAD_RISK_OFF, CryptoRegimeLabel.HIGH_DISPERSION}):
        return None
    entry, stop = geometry
    quality = (("btc_relative_strength", rs_btc), ("eth_relative_strength", rs_eth), ("regime_alignment", Decimal("1")))
    return _attempt(definition, context, reference=entry, trigger=entry, stop=stop, reasons=("positive excess return versus BTC and ETH",), quality=quality, observed=("btc_relative_strength", "eth_relative_strength", "regime"))


def _breadth_detector(definition: CryptoStrategyDefinition, context: CryptoDiscoveryContext) -> CryptoStrategyDetection | None:
    evidence = context.evidence
    geometry = _geometry(_bar_series(context))
    if geometry is None or not (_positive(evidence.btc_relative_strength.excess_return_percent) and _positive(evidence.eth_relative_strength.excess_return_percent)):
        return None
    if evidence.breadth.advancing_percent is None or evidence.breadth.advancing_percent < Decimal("50"):
        return None
    if evidence.regime.label in {CryptoRegimeLabel.BROAD_RISK_OFF, CryptoRegimeLabel.HIGH_DISPERSION}:
        return None
    entry, stop = geometry
    quality = (("breadth_support", evidence.breadth.advancing_percent), ("btc_relative_strength", evidence.btc_relative_strength.excess_return_percent or Decimal("0")), ("eth_relative_strength", evidence.eth_relative_strength.excess_return_percent or Decimal("0")))
    return _attempt(definition, context, reference=entry, trigger=entry, stop=stop, reasons=("trend, benchmark leadership, and breadth aligned",), quality=quality, observed=("breadth", "btc_relative_strength", "eth_relative_strength"))


_DETECTORS: dict[str, Callable[[CryptoStrategyDefinition, CryptoDiscoveryContext], CryptoStrategyDetection | None]] = {
    item.strategy_id: (_relative_strength_detector if item.strategy_id == "BTC_ETH_RELATIVE_STRENGTH_LEADERSHIP" else _breadth_detector if item.strategy_id == "BREADTH_ALIGNED_TREND_CONTINUATION" else _bar_detector)
    for item in active_crypto_strategies()
}


class _Detector:
    def __init__(self, definition: CryptoStrategyDefinition, fn: Callable[[CryptoStrategyDefinition, CryptoDiscoveryContext], CryptoStrategyDetection | None]) -> None:
        self.definition = definition
        self._fn = fn

    def detect(self, context: CryptoDiscoveryContext) -> CryptoStrategyDetection | None:
        return self._fn(self.definition, context)


def crypto_detector_registry() -> DetectorRegistry[CryptoDiscoveryContext, CryptoStrategyDetection | None]:
    return DetectorRegistry(tuple(_Detector(item, _DETECTORS[item.strategy_id]) for item in active_crypto_strategies()), maximum_detectors=len(active_crypto_strategies()))


def _rank_components(detection: CryptoStrategyDetection) -> tuple[ScoreComponent, ...]:
    return detection.score_components


def _rank_score(components: tuple[ScoreComponent, ...]) -> Decimal | None:
    values = tuple(item.value for item in components if item.availability is EvidenceAvailability.AVAILABLE and item.value is not None)
    return None if not values else sum(values, Decimal("0")) / Decimal(len(values))


class CryptoDiscoveryEngine:
    """Bounded detector evaluation and research-only opportunity aggregation."""

    def __init__(self, *, maximum_opportunities: int = MAX_DETECTIONS, maximum_signatures: int = MAX_SIGNATURES, maximum_episodes: int = MAX_EPISODES) -> None:
        if maximum_opportunities <= 0 or maximum_signatures <= 0 or maximum_episodes <= 0:
            raise ValueError("crypto discovery bounds must be positive")
        self.maximum_opportunities = maximum_opportunities
        self.maximum_signatures = maximum_signatures
        self.maximum_episodes = maximum_episodes
        self._signatures: OrderedDict[str, None] = OrderedDict()
        self._episodes: OrderedDict[str, None] = OrderedDict()
        self._duplicates = 0
        self._failures = 0

    @property
    def registry(self) -> DetectorRegistry[CryptoDiscoveryContext, CryptoStrategyDetection | None]:
        return crypto_detector_registry()

    def evaluate(self, contexts: tuple[CryptoDiscoveryContext, ...]) -> CryptoDiscoveryResult:
        detections: list[CryptoStrategyDetection] = []
        for context in contexts[:MAX_SYMBOLS]:
            for detector in self.registry.detectors:
                try:
                    result = detector.detect(context)
                except Exception:
                    self._failures += 1
                    continue
                if result is not None:
                    detections.append(result)
        # Keep every strategy-specific detection, while exposing one bounded
        # symbol-level aggregation view for overlap analysis.
        grouped: dict[str, list[CryptoStrategyDetection]] = {}
        for detection in detections[:MAX_DETECTIONS]:
            key = detection.identity.canonical_symbol
            grouped.setdefault(key, []).append(detection)
        opportunities: list[CryptoResearchOpportunity] = []
        for _, members in sorted(grouped.items(), key=lambda item: item[0]):
            primary = sorted(members, key=lambda item: item.strategy_id)[0]
            memberships = tuple(sorted({item.strategy_id for item in members}))
            identity = primary.identity
            opportunity = ResearchOpportunity(identity, primary.strategy_id, memberships, primary.detection.reference_price, primary.detection.structural_stop, primary.risk_geometry is not None)
            components = tuple(component for item in members for component in _rank_components(item))
            opportunities.append(CryptoResearchOpportunity(opportunity, memberships, tuple(members), components, _rank_score(components)))
            signature = semantic_digest(identity.canonical_symbol, memberships, tuple((item.detection.reference_price, item.detection.structural_stop) for item in members))
            if signature in self._signatures:
                self._duplicates += 1
            else:
                self._signatures[signature] = None
                self._signatures.move_to_end(signature)
                while len(self._signatures) > self.maximum_signatures:
                    self._signatures.popitem(last=False)
            self._episodes[signature] = None
            self._episodes.move_to_end(signature)
            while len(self._episodes) > self.maximum_episodes:
                self._episodes.popitem(last=False)
        opportunities.sort(key=lambda item: (item.rank_score is None, -(item.rank_score or Decimal("0")), item.opportunity.identity.canonical_symbol))
        opportunities = opportunities[: self.maximum_opportunities]
        active = sum(item.state is CryptoStrategyState.ACTIVE for item in _TAXONOMY)
        unavailable = sum(item.state is CryptoStrategyState.UNAVAILABLE_EVIDENCE for item in _TAXONOMY)
        metrics = CryptoDiscoveryMetrics(len(_TAXONOMY), active, unavailable, len(detections), len(opportunities), len(self._episodes), self._duplicates, self._failures, len(contexts[:MAX_SYMBOLS]))
        return CryptoDiscoveryResult(tuple(detections), tuple(opportunities), metrics)


__all__ = [
    "CryptoDiscoveryContext", "CryptoDiscoveryEngine", "CryptoDiscoveryMetrics",
    "CryptoDiscoveryResult", "CryptoResearchOpportunity", "CryptoStrategyDefinition",
    "CryptoStrategyDetection", "CryptoStrategyFamily", "CryptoStrategyState",
    "MAX_DETECTIONS", "MAX_EPISODES", "MAX_SIGNATURES", "MAX_SYMBOLS",
    "active_crypto_strategies", "crypto_detector_registry", "crypto_strategy_taxonomy",
]

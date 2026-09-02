"""Bounded incremental normalization of many detectors into opportunities."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from hashlib import sha256

from .contracts import (
    DetectionState, DiscoveryBatch, DiscoveryContext, NormalizedOpportunity,
    StrategyDetection, StrategyMembership,
)
from .detectors import DetectorRegistry, default_registry


@dataclass(frozen=True, slots=True)
class DiscoveryMetrics:
    market_observations: int
    detector_evaluations: int
    raw_detector_firings: int
    unique_detector_episodes: int
    normalized_opportunities: int
    average_strategies_per_opportunity: float
    maximum_strategies_per_opportunity: int
    tracked_symbols: int


def normalize_detections(detections: tuple[StrategyDetection, ...]) -> tuple[NormalizedOpportunity, ...]:
    detected = [item for item in detections if item.state in {
        DetectionState.DETECTED, DetectionState.STRENGTHENING,
    }]
    grouped = {}
    for item in detected:
        grouped.setdefault(item.opportunity_anchor, []).append(item)
    opportunities = []
    for anchor, rows in sorted(grouped.items()):
        ordered = tuple(sorted(rows, key=lambda item: item.strategy_id))
        memberships = tuple(StrategyMembership(
            item.strategy_id, item.strategy_version, item.family, item.state,
            item.detector_episode_id, item.setup_anchor, item.reference_price,
            item.trigger_level, item.structural_stop, item.reason_codes,
        ) for item in ordered)
        first = ordered[0]
        complete = [item for item in ordered if item.trigger_level is not None and item.structural_stop is not None]
        primary = complete[0] if complete else first
        identity = sha256(f"normalized-opportunity-v1|{anchor}".encode()).hexdigest()
        opportunities.append(NormalizedOpportunity(
            identity, first.symbol, first.session_date, first.session,
            max(item.decision_cutoff for item in ordered), anchor,
            primary.strategy_id, memberships, primary.reference_price,
            primary.structural_stop, bool(complete), True,
        ))
    return tuple(opportunities)


class MultiStrategyDiscoveryEngine:
    """Pure observation engine with bounded in-memory identity accounting."""

    def __init__(self, registry: DetectorRegistry | None = None, *, maximum_episodes: int = 10_000,
                 maximum_opportunities: int = 5_000, maximum_symbols: int = 1_000) -> None:
        if min(maximum_episodes, maximum_opportunities, maximum_symbols) <= 0:
            raise ValueError("discovery state limits must be positive")
        self.registry = registry or default_registry()
        self.maximum_episodes = maximum_episodes
        self.maximum_opportunities = maximum_opportunities
        self.maximum_symbols = maximum_symbols
        self._episodes = OrderedDict()
        self._opportunities: OrderedDict[str, NormalizedOpportunity] = OrderedDict()
        self._symbols = OrderedDict()
        self._observations = self._evaluations = self._firings = 0

    def observe(self, context: DiscoveryContext) -> DiscoveryBatch:
        self._observations += 1
        self._symbols[context.symbol.upper()] = context.decision_cutoff
        self._symbols.move_to_end(context.symbol.upper())
        _trim(self._symbols, self.maximum_symbols)
        detections = self.registry.evaluate(context)
        self._evaluations += len(detections)
        firings = tuple(item for item in detections if item.state in {DetectionState.DETECTED, DetectionState.STRENGTHENING})
        self._firings += len(firings)
        new_episodes = []
        for item in firings:
            if item.detector_episode_id not in self._episodes:
                new_episodes.append(item.detector_episode_id)
            self._episodes[item.detector_episode_id] = item.decision_cutoff
            self._episodes.move_to_end(item.detector_episode_id)
        _trim(self._episodes, self.maximum_episodes)
        current = normalize_detections(firings)
        new_opportunities = []
        merged = []
        for item in current:
            previous = self._opportunities.get(item.opportunity_id)
            if previous is None:
                new_opportunities.append(item.opportunity_id)
                value = item
            else:
                membership = {entry.strategy_id: entry for entry in previous.memberships}
                membership.update({entry.strategy_id: entry for entry in item.memberships})
                members = tuple(membership[key] for key in sorted(membership))
                primary = next(entry for entry in members if entry.strategy_id == previous.primary_strategy_id)
                value = replace(item, primary_strategy_id=previous.primary_strategy_id,
                                memberships=members, reference_price=primary.reference_price,
                                structural_stop=primary.structural_stop,
                                complete_r_plan=any(entry.trigger_level is not None and entry.structural_stop is not None for entry in members))
            self._opportunities[item.opportunity_id] = value
            self._opportunities.move_to_end(item.opportunity_id)
            merged.append(value)
        _trim(self._opportunities, self.maximum_opportunities)
        return DiscoveryBatch(detections, tuple(new_episodes), tuple(merged), tuple(new_opportunities))

    def metrics(self) -> DiscoveryMetrics:
        memberships = [len(item.memberships) for item in self._opportunities.values()]
        return DiscoveryMetrics(
            self._observations, self._evaluations, self._firings, len(self._episodes),
            len(self._opportunities), 0.0 if not memberships else sum(memberships) / len(memberships),
            max(memberships, default=0), len(self._symbols),
        )


def _trim(values: OrderedDict, limit: int) -> None:
    while len(values) > limit:
        values.popitem(last=False)

"""Coverage, overlap, cardinality, and plan-quality discovery reports."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict

from .contracts import DetectorAvailability, DetectionState


def strategy_discovery_report(registry, batches, metrics):
    batches = tuple(batches)
    detections = [item for batch in batches for item in batch.detections]
    opportunities = {}
    episodes = Counter()
    raw = Counter()
    for batch in batches:
        for item in batch.detections:
            if item.state in {DetectionState.DETECTED, DetectionState.STRENGTHENING}:
                raw[item.strategy_id] += 1
                episodes[(item.strategy_id, item.detector_episode_id)] += 1
        for item in batch.opportunities:
            opportunities[item.opportunity_id] = item
    coverage = []
    for detector in registry.detectors:
        definition = detector.definition
        relevant = [item for item in opportunities.values() if any(member.strategy_id == definition.strategy_id for member in item.memberships)]
        coverage.append({
            "strategy_id": definition.strategy_id,
            "family": definition.family.value,
            "status": definition.availability.value,
            "required_features": definition.required_features,
            "missing_features": definition.required_features if definition.availability is not DetectorAvailability.ACTIVE else (),
            "raw_detections": raw[definition.strategy_id],
            "unique_episodes": sum(strategy == definition.strategy_id for strategy, _ in episodes),
            "normalized_opportunities": len(relevant),
        })
    combinations = Counter(" + ".join(member.strategy_id for member in item.memberships) for item in opportunities.values())
    return {
        "strategy_coverage": coverage,
        "top_combinations": tuple(combinations.most_common(20)),
        "cardinality": asdict(metrics),
        "quality": {
            "valid_reference_price": sum(item.reference_price is not None for item in opportunities.values()),
            "valid_structural_stop": sum(item.structural_stop is not None for item in opportunities.values()),
            "complete_r_plan": sum(item.complete_r_plan for item in opportunities.values()),
            "sufficient_future_outcome_capability": sum(item.reference_price is not None for item in opportunities.values()),
        },
    }

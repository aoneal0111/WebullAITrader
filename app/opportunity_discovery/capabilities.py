"""Explicit feature-capability coverage for every registered hypothesis."""

from __future__ import annotations

from dataclasses import asdict

from .contracts import DetectorAvailability, FeatureCapabilities


def feature_capability_report(registry, capabilities: FeatureCapabilities = FeatureCapabilities()):
    result = []
    available_values = asdict(capabilities)
    for detector in registry.detectors:
        definition = detector.definition
        available = tuple(name for name in definition.required_features if available_values.get(name, False))
        unavailable = tuple(name for name in definition.required_features if not available_values.get(name, False))
        result.append({
            "strategy_id": definition.strategy_id,
            "required_features": definition.required_features,
            "optional_features": definition.optional_features,
            "currently_available": available,
            "currently_unavailable": unavailable,
            "status": definition.availability.value,
            "reason": definition.unavailable_reason,
            "observable": definition.availability is DetectorAvailability.ACTIVE and not unavailable,
        })
    return tuple(result)

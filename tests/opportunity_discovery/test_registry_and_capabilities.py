from collections import Counter

from app.opportunity_discovery import (
    DetectorAvailability, STRATEGY_TAXONOMY, StrategyFamily,
    default_registry, feature_capability_report,
)


def test_taxonomy_has_30_unique_versioned_research_only_hypotheses():
    assert len(STRATEGY_TAXONOMY) == 30
    assert len({item.strategy_id for item in STRATEGY_TAXONOMY}) == 30
    assert all(item.strategy_version and item.research_only for item in STRATEGY_TAXONOMY)
    assert all(isinstance(item.family, StrategyFamily) for item in STRATEGY_TAXONOMY)
    assert Counter(item.availability for item in STRATEGY_TAXONOMY) == {
        DetectorAvailability.ACTIVE: 23,
        DetectorAvailability.UNAVAILABLE_FEATURE: 4,
        DetectorAvailability.INSUFFICIENT_CONTEXT: 2,
        DetectorAvailability.FUTURE_RESEARCH: 1,
    }


def test_unavailable_detectors_disclose_exact_reason_and_capabilities():
    unavailable = [item for item in STRATEGY_TAXONOMY if item.availability is not DetectorAvailability.ACTIVE]
    assert all(item.unavailable_reason for item in unavailable)
    report = feature_capability_report(default_registry())
    assert len(report) == 30
    vwap = next(item for item in report if item["strategy_id"] == "VWAP_RECLAIM")
    assert vwap["currently_unavailable"] == ("authoritative_vwap",)
    assert not vwap["observable"]


def test_registration_cannot_grant_execution_authority():
    registry = default_registry()
    assert all(detector.definition.research_only for detector in registry.detectors)
    assert not any(hasattr(detector, name) for detector in registry.detectors
                   for name in ("place_order", "authorize_order", "veto_order", "resize_order"))

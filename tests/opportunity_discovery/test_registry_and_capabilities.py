from collections import Counter
from decimal import Decimal

from app.opportunity_discovery import (
    DetectionState, DetectorAvailability, STRATEGY_TAXONOMY, StrategyFamily,
    default_registry, feature_capability_report,
)
from app.opportunity_discovery.detectors import RuleDetector
from tests.opportunity_discovery.conftest import bar, context


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


def test_nonpositive_research_plan_preserves_detection_as_incomplete_evidence():
    definition = next(item for item in STRATEGY_TAXONOMY if item.availability is DetectorAvailability.ACTIVE)
    detector = RuleDetector(
        definition,
        lambda _analysis: (
            DetectionState.DETECTED,
            Decimal("10"),
            Decimal("10.01"),
            ("STRUCTURE_OBSERVED",),
            (),
        ),
    )
    detection = detector.detect(context((bar(0, 10, 10.2, 9.9, 10.1),)))
    assert detection.state is DetectionState.DETECTED
    assert detection.trigger_level == Decimal("10")
    assert detection.structural_stop is None
    assert "INSUFFICIENT_R_PLAN_NONPOSITIVE_RISK" in detection.reason_codes

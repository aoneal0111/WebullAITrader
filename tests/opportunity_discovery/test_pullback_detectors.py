from dataclasses import replace
from decimal import Decimal

from app.opportunity_discovery import DetectionState, default_registry
from tests.opportunity_discovery.conftest import bar, clean_pullback, context


def detections(bars):
    return {item.strategy_id: item for item in default_registry().evaluate(context(bars))}


def test_clean_first_pullback_matches_multiple_explainable_hypotheses():
    found = detections(clean_pullback())
    for identity in ("MICRO_PULLBACK", "FIRST_PULLBACK", "HIGHER_LOW_CONTINUATION",
                     "SHALLOW_PULLBACK_CONTINUATION", "VOLUME_CONTRACTION_PULLBACK"):
        assert found[identity].state is DetectionState.DETECTED
        assert found[identity].trigger_level is not None
        assert found[identity].structural_stop is not None


def test_deep_pullback_reclaim_and_dip_rip_are_distinct():
    bars = (
        bar(0, 10, 10.5, 9.95, 10.45, 2000), bar(1, 10.45, 11.1, 10.4, 11, 2500),
        bar(2, 11, 11.05, 10.25, 10.35, 800), bar(3, 10.35, 11.2, 10.3, 11.1, 1800),
    )
    found = detections(bars)
    assert found["DEEP_PULLBACK_RECLAIM"].state is DetectionState.DETECTED
    assert found["DIP_AND_RIP"].state is DetectionState.DETECTED


def test_pullback_below_impulse_origin_is_invalidated():
    bars = clean_pullback()
    damaged = bars[:3] + (replace(bars[3], low=Decimal("9.8")), bars[4])
    found = detections(damaged)
    assert found["FIRST_PULLBACK"].state is DetectionState.INVALIDATED


def test_momentum_reacceleration_requires_expanding_current_range():
    found = detections(clean_pullback())
    assert found["MOMENTUM_REACCELERATION"].state is DetectionState.DETECTED

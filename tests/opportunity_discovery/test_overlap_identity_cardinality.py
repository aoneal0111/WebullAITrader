from dataclasses import replace
from datetime import timedelta

from app.opportunity_discovery import MultiStrategyDiscoveryEngine, normalize_detections
from tests.opportunity_discovery.conftest import clean_pullback, context


def test_multiple_detector_matches_normalize_to_one_opportunity():
    engine = MultiStrategyDiscoveryEngine()
    batch = engine.observe(context(clean_pullback()))
    assert len(batch.opportunities) == 1
    opportunity = batch.opportunities[0]
    identities = {item.strategy_id for item in opportunity.memberships}
    assert {"FIRST_PULLBACK", "HIGHER_LOW_CONTINUATION", "VOLUME_CONTRACTION_PULLBACK"} <= identities
    assert len(opportunity.memberships) > 1


def test_quote_rank_and_scanner_changes_do_not_create_new_identity():
    engine = MultiStrategyDiscoveryEngine()
    original = context(clean_pullback(), percentage_change=25, spread_percent=1, scanner_rank=1)
    changed = replace(original, percentage_change=26, spread_percent=2, scanner_rank=50)
    first = engine.observe(original)
    second = engine.observe(changed)
    assert first.new_opportunity_ids
    assert second.new_opportunity_ids == ()
    assert {item.detector_episode_id for item in first.detections} == {
        item.detector_episode_id for item in second.detections
    }


def test_new_structural_window_creates_new_opportunity():
    engine = MultiStrategyDiscoveryEngine()
    first = engine.observe(context(clean_pullback()))
    shifted = tuple(replace(item, completed_at=item.completed_at + timedelta(minutes=20)) for item in clean_pullback())
    second = engine.observe(context(shifted, cutoff=shifted[-1].completed_at))
    assert first.new_opportunity_ids and second.new_opportunity_ids
    assert first.new_opportunity_ids != second.new_opportunity_ids


def test_same_structure_on_two_symbols_creates_two_opportunities():
    engine = MultiStrategyDiscoveryEngine()
    first = context(clean_pullback())
    bars = tuple(replace(item, symbol="WXYZ") for item in clean_pullback())
    second = context(bars, symbol="WXYZ")
    assert engine.observe(first).new_opportunity_ids
    assert engine.observe(second).new_opportunity_ids
    assert engine.metrics().normalized_opportunities == 2


def test_ten_thousand_observations_remain_opportunity_scale():
    engine = MultiStrategyDiscoveryEngine()
    base = context(clean_pullback())
    for rank in range(10_000):
        engine.observe(replace(base, scanner_rank=rank + 1))
    metrics = engine.metrics()
    assert metrics.market_observations == 10_000
    assert metrics.detector_evaluations == 300_000
    assert metrics.normalized_opportunities == 1
    assert metrics.unique_detector_episodes < 15
    assert metrics.raw_detector_firings > metrics.unique_detector_episodes

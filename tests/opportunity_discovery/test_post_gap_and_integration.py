from app.opportunity_discovery import (
    CompletedBar, DetectionState, NormalizedOpportunityObserved,
    default_registry, learning_membership_features, recommended_persistence_design,
)
from app.strategies.warrior_momentum.post_gap_reclaim_research import detect_post_gap_reclaim
from tests.opportunity_discovery.conftest import context
from tests.warrior_momentum.test_post_gap_reclaim_research import _aemd_bars, _context


def test_post_gap_reclaim_adapts_existing_research_semantics():
    source = _aemd_bars()[:10]
    existing = detect_post_gap_reclaim(source, _context())
    bars = tuple(CompletedBar(item.symbol, item.timestamp, item.open, item.high, item.low,
                              item.close, item.volume) for item in source)
    candidate = _context()
    result = next(item for item in default_registry().evaluate(context(
        bars, symbol=bars[0].symbol, percentage_change=candidate.percentage_change,
        relative_volume=candidate.relative_volume, dollar_volume=candidate.dollar_volume,
        spread_percent=candidate.spread_percent, float_shares=candidate.float_shares,
    )) if item.strategy_id == "POST_GAP_RECLAIM")
    assert "EXISTING_POST_GAP_RECLAIM_ADAPTER" in result.reason_codes
    assert result.trigger_level == (None if existing.plan is None else existing.plan.trigger)
    assert result.structural_stop == (None if existing.plan is None else existing.plan.stop)


def test_append_only_membership_design_preserves_old_experiences():
    from app.opportunity_discovery import MultiStrategyDiscoveryEngine
    from tests.opportunity_discovery.conftest import clean_pullback
    opportunity = MultiStrategyDiscoveryEngine().observe(context(clean_pullback())).opportunities[0]
    message = NormalizedOpportunityObserved(opportunity, opportunity.decision_cutoff)
    design = recommended_persistence_design()
    assert message.research_only
    assert design["choice"] == "SEPARATE_APPEND_ONLY_STRATEGY_MEMBERSHIP_RECORDS"
    assert not design["mutates_existing_experiences"]
    features = dict(learning_membership_features(opportunity))
    assert features["discovery_strategy_count"] == len(opportunity.memberships)
    assert "FIRST_PULLBACK" in features["discovery_strategy_combination"]


def test_missing_structural_stop_keeps_r_plan_unavailable():
    from dataclasses import replace
    from app.opportunity_discovery import MultiStrategyDiscoveryEngine
    from tests.opportunity_discovery.conftest import clean_pullback
    registry = default_registry()
    detected = tuple(item for item in registry.evaluate(context(clean_pullback()))
                     if item.state is DetectionState.DETECTED)
    stripped = tuple(replace(item, structural_stop=None) for item in detected)
    from app.opportunity_discovery import normalize_detections
    opportunity = normalize_detections(stripped)[0]
    assert not opportunity.complete_r_plan

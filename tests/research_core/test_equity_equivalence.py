from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json

from app.opportunity_discovery import (
    DetectionState,
    DetectorAvailability,
    FeatureCapabilities,
    MultiStrategyDiscoveryEngine,
    STRATEGY_TAXONOMY,
    default_registry,
)
from app.research_core import AssetResearchIdentity
from tests.opportunity_discovery.conftest import bar, clean_pullback, context
from tests.warrior_momentum.test_post_gap_reclaim_research import _aemd_bars, _context
from app.opportunity_discovery import CompletedBar


ACTIVE_ORDER = (
    "MICRO_PULLBACK",
    "FIRST_PULLBACK",
    "HIGHER_LOW_CONTINUATION",
    "SHALLOW_PULLBACK_CONTINUATION",
    "DEEP_PULLBACK_RECLAIM",
    "VOLUME_CONTRACTION_PULLBACK",
    "MOMENTUM_REACCELERATION",
    "HIGH_OF_DAY_BREAKOUT",
    "FLAT_TOP_BREAKOUT",
    "CONSOLIDATION_BREAKOUT",
    "ASCENDING_BASE_BREAKOUT",
    "RANGE_COMPRESSION_BREAKOUT",
    "BREAKOUT_RETEST_CONTINUATION",
    "OPENING_RANGE_BREAKOUT",
    "PREMARKET_HIGH_BREAKOUT",
    "PREMARKET_CONSOLIDATION_BREAKOUT",
    "OPENING_DRIVE_CONTINUATION",
    "FAILED_BREAKOUT_RECLAIM",
    "HOD_RECLAIM",
    "GAP_AND_GO_CONTINUATION",
    "POST_GAP_RECLAIM",
    "DIP_AND_RIP",
    "MOMENTUM_SQUEEZE_EXPANSION",
)


def _serialized_hash(value) -> str:
    payload = json.dumps(
        [asdict(item) for item in value],
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


def _representative_contexts():
    pullback = clean_pullback()
    deep = (
        bar(0, 10, 10.5, 9.95, 10.45, 2000),
        bar(1, 10.45, 11.1, 10.4, 11, 2500),
        bar(2, 11, 11.05, 10.25, 10.35, 800),
        bar(3, 10.35, 11.2, 10.3, 11.1, 1800),
    )
    flat = (
        bar(0, 10, 10.5, 9.9, 10.3),
        bar(1, 10.3, 10.51, 10.2, 10.4),
        bar(2, 10.4, 10.50, 10.25, 10.35),
        bar(3, 10.35, 10.505, 10.3, 10.4),
        bar(4, 10.4, 10.8, 10.38, 10.7, 2500),
    )
    compression = (
        bar(0, 10, 10.5, 9.9, 10.2, 1000),
        bar(1, 10.2, 10.45, 10.0, 10.3, 1000),
        bar(2, 10.3, 10.48, 10.15, 10.35, 900),
        bar(3, 10.35, 10.47, 10.25, 10.4, 800),
        bar(4, 10.4, 10.9, 10.35, 10.8, 2000),
    )
    premarket_consolidation = (
        bar(0, 10.2, 10.4, 10.15, 10.3, session="PREMARKET"),
        bar(1, 10.3, 10.42, 10.2, 10.35, session="PREMARKET"),
        bar(2, 10.35, 10.43, 10.22, 10.3, session="PREMARKET"),
        bar(3, 10.3, 10.41, 10.2, 10.35, session="PREMARKET"),
        bar(4, 10.35, 10.8, 10.3, 10.7, session="PREMARKET"),
    )
    retest = (
        bar(0, 10, 10.4, 9.9, 10.2),
        bar(1, 10.2, 10.5, 10.1, 10.4),
        bar(2, 10.4, 10.8, 10.35, 10.7),
        bar(3, 10.7, 10.72, 10.48, 10.55),
        bar(4, 10.55, 10.9, 10.52, 10.85),
    )
    reclaim = (
        bar(0, 10, 10.5, 9.9, 10.3),
        bar(1, 10.3, 10.7, 10.2, 10.45),
        bar(2, 10.45, 10.48, 10.1, 10.3),
        bar(3, 10.3, 10.8, 10.25, 10.75),
    )
    premarket = tuple(
        bar(i, 10, 10.4 + i / 100, 9.9, 10.2, session="PREMARKET")
        for i in range(3)
    )
    regular = tuple(
        bar(3 + i, 10.3, 10.5, 10.2, 10.4, session="REGULAR")
        for i in range(5)
    )
    sessions = premarket + regular + (
        bar(8, 10.4, 10.8, 10.35, 10.7, session="REGULAR"),
    )
    source = _aemd_bars()[:12]
    post_gap = tuple(
        CompletedBar(
            item.symbol,
            item.timestamp,
            item.open,
            item.high,
            item.low,
            item.close,
            item.volume,
        )
        for item in source
    )
    candidate = _context()
    return {
        "empty": context((bar(0, 10, 10.2, 9.9, 10.1),)),
        "pullback": context(pullback),
        "deep": context(deep),
        "flat": context(flat),
        "compression": context(compression),
        "premarket_consolidation": context(premarket_consolidation),
        "retest": context(retest),
        "reclaim": context(reclaim),
        "sessions": context(
            sessions,
            capabilities=FeatureCapabilities(prior_close=True),
            prior_close=Decimal("9.5"),
        ),
        "post_gap_trigger": context(
            post_gap,
            symbol=post_gap[0].symbol,
            percentage_change=candidate.percentage_change,
            relative_volume=candidate.relative_volume,
            dollar_volume=candidate.dollar_volume,
            spread_percent=candidate.spread_percent,
            float_shares=candidate.float_shares,
        ),
    }


# Current contract includes TRIGGER_ARMED facts (f66cc6f) and session-root
# structural provenance/anchors (bdadd17). DETECTED conditions are unchanged.
GOLDEN_OUTPUT_HASHES = {
    "empty": "726100c518f3f64ff1d2dfd8766f539b5a7da2be9026d93c48d31a1c12dad673",
    "pullback": "c63965cdb8c317ceafb4e5cf952892da90305f329a083316e8be81c326cf5fa4",
    "deep": "ff56529756af7ddb29ba6fc6786092547b7d5d399e9031030597363ad594cc98",
    "flat": "32717d9c435013cf214273577bcda965d97b4153867dab9e33d5e10dd276fe51",
    "compression": "ce8a828a0f51485e8e6ddbd1b1ab51a0f9acdd9a257ccf972a69a4fd1856643c",
    "premarket_consolidation": "bd2cbe9cc861fd83a6897d1b34c2592191494f6c4225c0c27c50b3d5831c6e16",
    "retest": "6148470f092a0ab8fa8bb9d848f06f7640f57b3768b8a6e6bd72856059b5da19",
    "reclaim": "1ead25c5587c1761e59b6a05b2311f58e268cbecb622bc68f6a28f9033bd3004",
    "sessions": "2b4c0f3c76501bb47514c2ab445a5b0620b36c8d1d5c99b29b1a997a310a5bff",
    "post_gap_trigger": "25d5cb2da191ede353de6cdc859fc585dcc66d9493ab6e2514e0efcc6cb63d0b",
}


def test_all_equity_detector_outputs_match_current_golden_contracts():
    registry = default_registry()
    detected: set[str] = set()
    for name, ctx in _representative_contexts().items():
        first = registry.evaluate(ctx)
        second = registry.evaluate(ctx)
        assert first == second
        assert _serialized_hash(first) == GOLDEN_OUTPUT_HASHES[name]
        assert len(first) == 30
        detected.update(
            item.strategy_id for item in first
            if item.state is DetectionState.DETECTED
        )
        for item in first:
            shared = item.to_research_detection()
            assert shared.identity.lifecycle_id == item.detector_episode_id
            assert shared.identity.deterministic_id == item.to_research_detection().identity.deterministic_id
            assert shared.family == item.family.value
            assert shared.state == item.state.value
            assert shared.reference_price == item.reference_price
            assert shared.trigger_level == item.trigger_level
            assert shared.structural_stop == item.structural_stop
            assert shared.quality_components == item.quality_components
            assert shared.observed_evidence == (
                item.required_features_observed + item.optional_features_observed
            )
            assert shared.missing_evidence == item.missing_features
            assert shared.reasons == item.reason_codes
            assert shared.research_only == item.research_only
    assert detected == set(ACTIVE_ORDER)


def test_registry_order_and_taxonomy_are_exactly_preserved():
    registry = default_registry()
    registered = tuple(item.definition for item in registry.detectors)
    active = tuple(
        item.strategy_id for item in registered
        if item.availability is DetectorAvailability.ACTIVE
    )
    inactive = tuple(
        item.strategy_id for item in registered
        if item.availability is not DetectorAvailability.ACTIVE
    )
    assert registered == STRATEGY_TAXONOMY
    assert len(registered) == 30
    assert active == ACTIVE_ORDER
    assert len(inactive) == 7


def test_equity_opportunity_adapter_preserves_existing_identity_and_geometry():
    opportunity = MultiStrategyDiscoveryEngine().observe(
        context(clean_pullback())
    ).opportunities[0]
    shared = opportunity.to_research_opportunity()
    assert isinstance(shared.identity, AssetResearchIdentity)
    assert shared.identity.opportunity_id == opportunity.opportunity_id
    assert shared.primary_strategy_id == opportunity.primary_strategy_id
    assert shared.strategy_memberships == tuple(
        item.strategy_id for item in opportunity.memberships
    )
    assert shared.reference_price == opportunity.reference_price
    assert shared.structural_stop == opportunity.structural_stop
    assert shared.complete_r_plan == opportunity.complete_r_plan
    assert shared.research_only == opportunity.research_only

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


GOLDEN_OUTPUT_HASHES = {
    "empty": "af5f8c5b6651b624ee2614d3e903dc27bdaca851cd734e4d2a38163285cdc08a",
    "pullback": "770d9543501b92ac159f99e8c9794a669f81a4f17948e835fca2dd6c171cbe6b",
    "deep": "45475f52f393740d573271c4368c087dcceb58fcddc7a71cb7b9acb723f47e04",
    "flat": "dfa33e86974c47931158e5ed02f63176c94008e7a6080b1727ce5a05b84064d6",
    "compression": "fe4db13afbe79328b399bf5ec91a007bba1af9a8a389ab2dd08f92e5f87f8040",
    "premarket_consolidation": "73dc139b974af53f20187eeda48b6f1959b10d3c91232f846803e5c1a85ca7c6",
    "retest": "698a9f2096eeaea5e87bbc0bff4fba2f1d4ec836085a04a02c6edfa5218a0609",
    "reclaim": "24611879582d4c6a8b73aa0232e80180deb085709c17d551bd35f12846fe68eb",
    "sessions": "a3aaeb34aa1a8e46aedd9018ef3234794aab38cb9c180a83d2efcb8ae0bf4780",
    "post_gap_trigger": "c1a0167b77b5769cc5f43e0477f6e3bcf1c1c76b8b2e419e72a254a2bf0e4d30",
}


def test_all_equity_detector_outputs_match_pre_refactor_golden_contracts():
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

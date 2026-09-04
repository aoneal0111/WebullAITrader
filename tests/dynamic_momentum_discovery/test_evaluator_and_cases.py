from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.dynamic_momentum_discovery import (
    DiscoverySource,
    MomentumEvent,
    ProductionUniverseComparison,
    SourceMembership,
    evaluate_dynamic_momentum,
)
from app.dynamic_momentum_discovery.evaluator import production_comparison
from tests.dynamic_momentum_discovery.helpers import D, NOW, snapshot


def test_lightweight_features_and_momentum_events_are_explainable():
    result = evaluate_dynamic_momentum(snapshot())
    assert result.features.change_percent == D("25")
    assert result.features.gap_percent == D("12.500")
    assert result.features.dollar_volume == D("5000000")
    assert result.features.spread == D("0.02")
    assert result.features.spread_percent == D("0.2")
    assert result.features.top_of_book_liquidity == D("1500")
    assert set(result.events) >= {
        MomentumEvent.ABNORMAL_VOLUME_ACCELERATION,
        MomentumEvent.PRICE_ACCELERATION,
        MomentumEvent.SESSION_HIGH_BREAKOUT,
        MomentumEvent.GAP_EXPANSION,
        MomentumEvent.LIQUIDITY_EMERGENCE,
        MomentumEvent.MOMENTUM_PERSISTENCE,
    }
    assert result.shadow_promote_to_full_analysis is True
    assert result.production_promoted is False
    assert result.selection_authorized is False
    assert result.execution_authorized is False


def test_reacceleration_uses_only_earlier_point_in_time_snapshot():
    previous = snapshot(recent_1m_change_percent=D("1"))
    current = snapshot(
        decision_cutoff=NOW + timedelta(minutes=1),
        quote_timestamp=NOW + timedelta(seconds=59),
        recent_1m_change_percent=D("2.5"),
    )
    result = evaluate_dynamic_momentum(current, previous=previous)
    assert MomentumEvent.MOMENTUM_REACCELERATION in result.events


def test_wide_spread_junk_spike_is_not_shadow_promoted():
    junk = snapshot(
        symbol="JUNK", relative_volume=None, volume=D("100"),
        bid=D("5"), ask=D("10"), bid_size=D("1"), ask_size=D("1"),
        recent_5m_change_percent=None, volume_acceleration=None,
        fresh_high_count=0, prior_session_high=None,
        memberships=(snapshot().memberships[0],),
    )
    result = evaluate_dynamic_momentum(junk)
    assert MomentumEvent.LIQUIDITY_EMERGENCE not in result.events
    assert result.shadow_promote_to_full_analysis is False


def test_thin_liquidity_spike_does_not_receive_liquidity_component():
    result = evaluate_dynamic_momentum(snapshot(
        symbol="THIN", bid_size=D("10"), ask_size=D("10"),
    ))
    liquidity = next(item for item in result.components if item.name == "LIQUIDITY")
    assert liquidity.available is True
    assert liquidity.points == 0


def test_sustained_continuation_and_gap_fade_are_distinct_decision_cases():
    sustained = evaluate_dynamic_momentum(snapshot(symbol="SUST"))
    fade_candidate = evaluate_dynamic_momentum(snapshot(
        symbol="FADE", recent_5m_change_percent=D("-4"),
        volume_acceleration=D("0.5"), fresh_high_count=0,
        prior_session_high=None, memberships=(snapshot().memberships[0],),
    ))
    assert sustained.shadow_promote_to_full_analysis is True
    assert fade_candidate.shadow_score < sustained.shadow_score


def test_imrn_page_two_fixture_is_discoverable_without_production_visibility():
    # Synthetic point-in-time fixture: it tests the page-two research boundary.  It is
    # not asserted to reproduce unavailable historical IMRN market fields.
    imrn = snapshot(
        symbol="IMRN", session="REGULAR", production_stages=(),
        memberships=(SourceMembership(DiscoverySource.SESSION_GAINERS, 60, 2),),
    )
    result = evaluate_dynamic_momentum(imrn)
    assert imrn.memberships[0].rank == 60
    assert imrn.memberships[0].page_index == 2
    assert result.production_comparison is ProductionUniverseComparison.PRODUCTION_NOT_RETURNED
    assert result.shadow_promote_to_full_analysis is True


@pytest.mark.parametrize(("stages", "expected"), [
    (("DAY_GAINERS",), ProductionUniverseComparison.PRODUCTION_RETURNED_GAINERS),
    (("RELATIVE_VOLUME_10D",), ProductionUniverseComparison.PRODUCTION_RETURNED_RVOL),
    (("DAY_GAINERS", "RELATIVE_VOLUME_10D"), ProductionUniverseComparison.PRODUCTION_RETURNED_BOTH),
    (("NORMALIZATION_REJECTED",), ProductionUniverseComparison.PRODUCTION_NORMALIZATION_REJECTED),
    (("REFERENCE_WARMUP_REJECTED",), ProductionUniverseComparison.PRODUCTION_REFERENCE_REJECTED),
    (("UNIVERSE_ADMITTED",), ProductionUniverseComparison.PRODUCTION_ADMITTED),
    (("SCANNER_EVALUATION_REACHED",), ProductionUniverseComparison.PRODUCTION_SCANNER_REACHED),
])
def test_production_universe_observability_comparison(stages, expected):
    assert production_comparison(stages) is expected


def test_future_quote_and_future_acceleration_evidence_are_rejected():
    with pytest.raises(ValueError, match="cannot exceed decision cutoff"):
        snapshot(quote_timestamp=NOW + timedelta(seconds=1))
    with pytest.raises(ValueError, match="cannot exceed decision cutoff"):
        snapshot(first_acceleration_at=NOW + timedelta(seconds=1))

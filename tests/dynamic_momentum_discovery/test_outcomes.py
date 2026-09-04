from __future__ import annotations

from datetime import timedelta

import pytest

from app.dynamic_momentum_discovery import (
    ForwardMarketPoint,
    evaluate_dynamic_momentum,
    label_dynamic_momentum_outcome,
)
from app.dynamic_momentum_discovery.experiments import summarize_selectivity
from tests.dynamic_momentum_discovery.helpers import D, NOW, snapshot


def test_forward_outcomes_are_separate_labels_for_continuation():
    observation = evaluate_dynamic_momentum(snapshot(symbol="SUST"))
    points = (
        ForwardMarketPoint(NOW + timedelta(minutes=5), D("10.5"), D("10.49"), D("10.51")),
        ForwardMarketPoint(NOW + timedelta(minutes=15), D("11"), D("10.99"), D("11.01")),
        ForwardMarketPoint(NOW + timedelta(minutes=30), D("12"), D("11.98"), D("12.02")),
    )
    outcome = label_dynamic_momentum_outcome(
        observation, points, labeled_at=NOW + timedelta(minutes=31)
    )
    assert outcome.return_5m_percent == D("5.00")
    assert outcome.return_15m_percent == D("10.0")
    assert outcome.return_30m_percent == D("20.0")
    assert outcome.maximum_favorable_excursion_percent == D("20.0")
    assert outcome.new_high_continuation is True
    assert outcome.execution_authorized is False


def test_gap_and_fade_outcome_does_not_rewrite_discovery_observation():
    observation = evaluate_dynamic_momentum(snapshot(symbol="FADE"))
    outcome = label_dynamic_momentum_outcome(
        observation,
        (ForwardMarketPoint(NOW + timedelta(minutes=5), D("9")),),
        labeled_at=NOW + timedelta(minutes=6),
    )
    assert outcome.fade is True
    assert outcome.breakout_failure is True
    assert observation.snapshot.price == D("10")
    assert observation.shadow_promote_to_full_analysis is True


def test_outcome_labeler_rejects_cutoff_or_lookahead_contamination():
    observation = evaluate_dynamic_momentum(snapshot())
    with pytest.raises(ValueError, match="strictly after"):
        label_dynamic_momentum_outcome(
            observation, (ForwardMarketPoint(NOW, D("11")),),
            labeled_at=NOW + timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="cannot exceed label time"):
        label_dynamic_momentum_outcome(
            observation,
            (ForwardMarketPoint(NOW + timedelta(minutes=2), D("11")),),
            labeled_at=NOW + timedelta(minutes=1),
        )


def test_selectivity_reports_labeled_false_positive_burden():
    continuation = evaluate_dynamic_momentum(snapshot(symbol="GOOD"))
    false_positive = evaluate_dynamic_momentum(snapshot(symbol="FADE"))
    junk = evaluate_dynamic_momentum(snapshot(
        symbol="JUNK", relative_volume=None, volume=D("10"),
        recent_5m_change_percent=None, volume_acceleration=None,
        fresh_high_count=0, prior_session_high=None,
        memberships=(snapshot().memberships[0],),
    ))
    good_outcome = label_dynamic_momentum_outcome(
        continuation, (ForwardMarketPoint(NOW + timedelta(minutes=5), D("11")),),
        labeled_at=NOW + timedelta(minutes=6),
    )
    bad_outcome = label_dynamic_momentum_outcome(
        false_positive, (ForwardMarketPoint(NOW + timedelta(minutes=5), D("9")),),
        labeled_at=NOW + timedelta(minutes=6),
    )
    result = summarize_selectivity(
        (continuation, false_positive, junk), (good_outcome, bad_outcome)
    )
    assert result.episode_count == 3
    assert result.promoted_count == 2
    assert result.positive_5m_count == 1
    assert result.precision_proxy == D("0.5")

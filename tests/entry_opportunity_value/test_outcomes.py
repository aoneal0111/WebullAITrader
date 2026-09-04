from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.entry_opportunity_value import ForwardPricePoint, evaluate_entry_opportunity, label_forward_outcomes
from tests.entry_opportunity_value.test_evaluator import CUTOFF, entry_context


D = Decimal


def test_original_current_and_bounded_outcomes_remain_separate_labels():
    observation = evaluate_entry_opportunity(entry_context(
        bid=D("10.09"), ask=D("10.10"),
        expected_remaining_move=D("0.40"), expected_remaining_move_basis="completed-bar range",
        expected_remaining_move_observed_at=CUTOFF,
    ))
    points = (
        ForwardPricePoint(CUTOFF + timedelta(minutes=1), D("10.05"), high=D("10.12"), low=D("9.99")),
        ForwardPricePoint(CUTOFF + timedelta(minutes=5), D("10.30"), high=D("10.32"), low=D("10.03")),
        ForwardPricePoint(CUTOFF + timedelta(minutes=15), D("10.40"), high=D("10.45"), low=D("10.25")),
        ForwardPricePoint(CUTOFF + timedelta(minutes=30), D("10.20"), high=D("10.42"), low=D("10.18")),
    )
    labels = label_forward_outcomes(
        observation, points, actual_fill_status="UNFILLED", actual_trade_status="NO_TRADE"
    )
    assert labels.original_plan_outcome.plan == "ORIGINAL_PLAN_OUTCOME"
    assert labels.current_market_entry_outcome.plan == "CURRENT_MARKET_ENTRY_OUTCOME"
    assert labels.bounded_reprice_hypothesis_outcomes[0].plan == "BOUNDED_REPRICE_HYPOTHESIS_OUTCOME"
    assert labels.original_plan_outcome.entry_reference == D("10.00")
    assert labels.current_market_entry_outcome.entry_reference == D("10.10")
    assert labels.labels_only is True
    assert labels.execution_authorized is False
    assert observation.context.expected_remaining_move == D("0.40")


def test_future_labels_reject_cutoff_or_out_of_order_points():
    observation = evaluate_entry_opportunity(entry_context())
    with pytest.raises(ValueError, match="after the decision cutoff"):
        label_forward_outcomes(observation, (ForwardPricePoint(CUTOFF, D("10")),))
    with pytest.raises(ValueError, match="strictly ordered"):
        label_forward_outcomes(observation, (
            ForwardPricePoint(CUTOFF + timedelta(seconds=2), D("10")),
            ForwardPricePoint(CUTOFF + timedelta(seconds=1), D("10")),
        ))


def test_original_patient_limit_can_remain_hypothetically_unfilled():
    observation = evaluate_entry_opportunity(entry_context(bid=D("10.19"), ask=D("10.20")))
    labels = label_forward_outcomes(observation, (
        ForwardPricePoint(CUTOFF + timedelta(minutes=5), D("10.30"), low=D("10.15"), high=D("10.35")),
    ))
    assert labels.original_plan_outcome.hypothetical_fillable is False
    assert labels.current_market_entry_outcome.hypothetical_fillable is True
    assert labels.original_plan_outcome.mfe is None

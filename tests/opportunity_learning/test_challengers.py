from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.opportunity_learning.challengers import (
    CalibratedScoreChallenger, EmpiricalCohortChallenger,
    HistoricalAnalogChallenger, SimpleLogisticChallenger,
)
from app.opportunity_learning.contracts import LearningTarget, ResearchEvidencePolicy
from app.opportunity_learning.dataset import build_learning_examples
from app.opportunity_learning.evaluation import compare_champion, evaluate_challenger, select_on_validation
from app.trade_intelligence.models import AtlasDecision
from app.trade_intelligence.outcome_engine import OutcomeEngine
from tests.trade_intelligence.conftest import make_experience
from tests.trade_intelligence.test_outcomes_and_analogs import path


def tiny_policy():
    return ResearchEvidencePolicy(minimum_total=6, minimum_train=2, minimum_validation=2,
        minimum_holdout=2, minimum_positive=1, minimum_negative=1, minimum_unique_dates=3,
        minimum_unique_symbols=3, minimum_unique_sessions=1, minimum_cohort=1,
        minimum_analogs=1, examples_per_fitted_feature=1)


def examples():
    result = []
    dates = (datetime(2026, 6, 10, 14, tzinfo=UTC), datetime(2026, 6, 11, 14, tzinfo=UTC),
             datetime(2026, 7, 10, 14, tzinfo=UTC), datetime(2026, 7, 11, 14, tzinfo=UTC),
             datetime(2026, 8, 10, 14, tzinfo=UTC), datetime(2026, 8, 11, 14, tzinfo=UTC))
    for index, at in enumerate(dates):
        exp = make_experience(symbol=f"S{index}", episode=str(index), at=at,
                              decision=AtlasDecision.ENTRY_READY if index % 2 else AtlasDecision.REJECTED,
                              blockers=() if index % 2 else ("SPREAD_WIDE",), traded=index % 2 == 1)
        bars = tuple(replace(bar, timestamp=bar.timestamp - path([]).__len__() * timedelta(0) + (at - datetime(2026, 8, 31, 14, 30, tzinfo=UTC)))
                     for bar in path([(Decimal("11.2"), Decimal("9.8"), Decimal("11"))] if index % 2 else [(Decimal("10.2"), Decimal("9.4"), Decimal("9.6"))], symbol=f"S{index}"))
        outcome = OutcomeEngine().evaluate(exp, bars)
        result.extend(build_learning_examples((exp,), outcome))
    return tuple(result)


def test_all_challengers_are_deterministic_and_probability_bounded():
    data = examples()
    train = tuple(item for item in data if item.partition.value == "TRAIN")
    validation = tuple(item for item in data if item.partition.value == "VALIDATION")
    empirical = EmpiricalCohortChallenger(train, LearningTarget.ONE_R_BEFORE_STOP, tiny_policy())
    logistic_a = SimpleLogisticChallenger.fit(data, LearningTarget.ONE_R_BEFORE_STOP, tiny_policy())
    logistic_b = SimpleLogisticChallenger.fit(data, LearningTarget.ONE_R_BEFORE_STOP, tiny_policy())
    assert tuple(logistic_a.weights) == tuple(logistic_b.weights)
    for challenger in (empirical, logistic_a, CalibratedScoreChallenger.fit(logistic_a, validation)):
        prediction = challenger.predict(data[-1].features)
        assert prediction.probability is None or 0 <= prediction.probability <= 1
        assert prediction.recommendation.value.startswith("RESEARCH_")


def test_holdout_is_rejected_for_fitting_or_calibration():
    data = examples()
    holdout = tuple(item for item in data if item.partition.value == "HOLDOUT")
    with pytest.raises(ValueError, match="TRAIN"):
        EmpiricalCohortChallenger(holdout, LearningTarget.ONE_R_BEFORE_STOP, tiny_policy())
    base = SimpleLogisticChallenger.fit(data, LearningTarget.ONE_R_BEFORE_STOP, tiny_policy())
    with pytest.raises(ValueError, match="HOLDOUT"):
        CalibratedScoreChallenger.fit(base, holdout)
    with pytest.raises(ValueError, match="VALIDATION"):
        select_on_validation((base,), holdout)


def test_analog_never_uses_future_and_explains_dimensions():
    data = examples()
    challenger = HistoricalAnalogChallenger(data, LearningTarget.ONE_R_BEFORE_STOP, tiny_policy())
    earliest = challenger.predict(data[0].features)
    assert earliest.sample_size == 0
    later = challenger.predict(data[-1].features)
    assert all(item.startswith("matched:") for item in later.explanation)


def test_default_gate_returns_insufficient_instead_of_fitting_tiny_data():
    data = examples()
    model = SimpleLogisticChallenger.fit(data, LearningTarget.TWO_R_BEFORE_STOP)
    prediction = model.predict(data[0].features)
    assert prediction.evidence_status.value == "INSUFFICIENT_EVIDENCE"
    assert prediction.probability is None


def test_metrics_calibration_and_champion_comparison_are_exact_shapes():
    data = examples()
    model = SimpleLogisticChallenger.fit(data, LearningTarget.ONE_R_BEFORE_STOP, tiny_policy())
    metrics = evaluate_challenger(model, tuple(item for item in data if item.partition.value == "HOLDOUT"))
    assert metrics.sample_size == 2 and len(metrics.calibration) == 10
    assert metrics.brier_score is not None and metrics.log_loss is not None
    comparison = compare_champion(model, data)
    assert comparison.champion_only + comparison.challenger_only + comparison.both + comparison.neither == len(data)

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.opportunity_learning.cohorts import analyze_blockers, analyze_feature_cohorts
from app.opportunity_learning.contracts import ResearchEvidencePolicy
from app.opportunity_learning.dataset import build_learning_examples
from app.trade_intelligence.models import AtlasDecision, DecisionObservation
from app.trade_intelligence.outcome_engine import OutcomeEngine
from tests.trade_intelligence.conftest import T0, make_experience
from tests.trade_intelligence.test_outcomes_and_analogs import path


def policy():
    return ResearchEvidencePolicy(minimum_total=1, minimum_train=1, minimum_validation=1,
        minimum_holdout=1, minimum_positive=1, minimum_negative=1, minimum_unique_dates=1,
        minimum_unique_symbols=1, minimum_unique_sessions=1, minimum_cohort=1,
        minimum_analogs=1, examples_per_fitted_feature=1)


def test_blocker_context_distinguishes_sole_multiple_cleared_and_persistent():
    exp = make_experience(blockers=("NO_CATALYST",))
    first = DecisionObservation(exp.experience_id, T0, "a", AtlasDecision.REJECTED,
        exp.snapshot, ("NO_CATALYST",), False, False, exp.key.symbol, "FORMING")
    later_snap = replace(exp.snapshot, decision_timestamp=T0 + timedelta(minutes=1), source_timestamp=T0 + timedelta(minutes=1))
    later = DecisionObservation(exp.experience_id, later_snap.decision_timestamp, "b",
        AtlasDecision.TRIGGERED, later_snap, ("SPREAD_WIDE",), True, False, exp.key.symbol, "TRIGGERED")
    outcomes = OutcomeEngine().evaluate(exp, path([(Decimal("11.1"), Decimal("9.8"), Decimal("11"))]))
    cohorts = analyze_blockers(build_learning_examples((exp,), outcomes, (first, later)), policy())
    contexts = {(item.blocker, item.context) for item in cohorts}
    assert ("NO_CATALYST", "INITIAL") in contexts
    assert ("NO_CATALYST", "CLEARED") in contexts
    assert ("SPREAD_WIDE", "PERSISTENT") in contexts
    assert all(item.causal_claim_allowed == (item.context == "SOLE") for item in cohorts)


def test_pullback_combination_reports_exact_count_and_rates():
    exp = make_experience()
    outcomes = OutcomeEngine().evaluate(exp, path([(Decimal("11.1"), Decimal("9.8"), Decimal("11"))]))
    examples = build_learning_examples((exp,), outcomes)
    result = analyze_feature_cohorts(examples, ("setup_type", "pullback_depth_percent"), policy())
    assert len(result) == 1 and result[0].sample_size == 1
    assert result[0].one_r_rate == 1 and result[0].two_r_rate == 1
    assert result[0].one_r_confidence_interval is not None

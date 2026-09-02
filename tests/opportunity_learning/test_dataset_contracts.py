from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.opportunity_learning.contracts import LearningTarget, ResearchEvidencePolicy
from app.opportunity_learning.dataset import (
    assess_sufficiency, build_learning_examples, feature_digest, labels_from_outcomes,
)
from app.trade_intelligence.models import (
    AtlasDecision, DecisionObservation, FEATURE_VERSION, HorizonOutcome,
    OutcomeKind, OutcomeStatus, ResearchGeneration, SCHEMA_VERSION,
)
from app.trade_intelligence.outcome_engine import OutcomeEngine
from tests.trade_intelligence.conftest import T0, make_experience
from tests.trade_intelligence.test_outcomes_and_analogs import path


def test_missing_is_explicit_and_zero_is_not_substituted():
    exp = make_experience()
    exp = replace(exp, snapshot=replace(exp.snapshot, relative_volume=None, float_shares=None))
    example = build_learning_examples((exp,), ())[0]
    values = example.features.as_mapping()
    assert values["relative_volume"] is None
    assert values["float_shares"] is None
    assert "relative_volume" in example.features.missing
    assert "float_shares" in example.features.missing


def test_later_decision_cannot_change_earlier_vector():
    exp = make_experience()
    initial = DecisionObservation(
        exp.experience_id, T0, "initial", AtlasDecision.FORMING, exp.snapshot,
        ("NO_CATALYST",), False, False, exp.key.symbol, "FORMING",
    )
    later_snapshot = replace(
        exp.snapshot, decision_timestamp=T0 + timedelta(minutes=2),
        source_timestamp=T0 + timedelta(minutes=2), last_price=Decimal("12"),
        feature_source_timestamps=tuple((name, T0) for name, _ in exp.snapshot.features),
    )
    later = DecisionObservation(
        exp.experience_id, later_snapshot.decision_timestamp, "later",
        AtlasDecision.TRIGGERED, later_snapshot, (), True, False, exp.key.symbol, "TRIGGERED",
    )
    before = build_learning_examples((exp,), (), (initial,))[0]
    after = build_learning_examples((exp,), (), (initial, later))[0]
    assert feature_digest(before.features) == feature_digest(after.features)
    assert before.features.as_mapping()["last_price"] == 10.0
    assert len(build_learning_examples((exp,), (), (initial, later))) == 2


def test_future_feature_timestamp_is_rejected_by_memory_contract():
    exp = make_experience()
    with pytest.raises(ValueError, match="anti-lookahead"):
        replace(exp.snapshot, feature_source_timestamps=(("future", T0 + timedelta(microseconds=1)),))


def test_labels_are_exact_and_incomplete_is_censored():
    exp = make_experience()
    profitable = OutcomeEngine().evaluate(exp, path([(Decimal("11.6"), Decimal("9.8"), Decimal("11.5"))]))[0]
    labels = labels_from_outcomes((profitable,))
    assert labels.one_r_before_stop and labels.two_r_before_stop and labels.three_r_before_stop
    assert not labels.stop_before_one_r and labels.expected_return_r == 3.0
    stopped = OutcomeEngine().evaluate(exp, path([(Decimal("11.6"), Decimal("9.4"), Decimal("10"))]))[0]
    stopped_labels = labels_from_outcomes((stopped,))
    assert stopped_labels.stop_before_one_r and stopped_labels.expected_return_r == -1.0
    incomplete = replace(
        profitable, status=OutcomeStatus.INSUFFICIENT_DATA, future_price=None,
        reached_1r=None, reached_2r=None, reached_3r=None, stop_reached=None,
        plan_outcome_kind=None,
    )
    assert labels_from_outcomes((incomplete,)) is None


def test_generation_partitions_are_temporal_and_same_session_indivisible():
    generation = ResearchGeneration(
        "G", "ATLAS_TEMPORAL_SPLIT_V1", date(2026, 6, 1), date(2026, 6, 30),
        date(2026, 7, 1), date(2026, 7, 31), date(2026, 8, 1), date(2026, 8, 31),
        date(2026, 8, 31), FEATURE_VERSION, SCHEMA_VERSION,
        datetime(2026, 9, 1, tzinfo=UTC),
    )
    values = tuple(make_experience(symbol=f"S{i}", episode=str(i), at=at) for i, at in enumerate((
        datetime(2026, 6, 15, 14, tzinfo=UTC), datetime(2026, 7, 15, 14, tzinfo=UTC),
        datetime(2026, 8, 15, 14, tzinfo=UTC), datetime(2026, 8, 15, 19, tzinfo=UTC),
    )))
    examples = build_learning_examples(values, (), generation=generation)
    assert [item.partition.value for item in examples] == ["TRAIN", "VALIDATION", "HOLDOUT", "HOLDOUT"]


def test_small_realistic_dataset_reports_insufficient_evidence():
    examples = build_learning_examples((make_experience(),), ())
    result = assess_sufficiency(examples, LearningTarget.ONE_R_BEFORE_STOP, ResearchEvidencePolicy())
    assert result.status.value == "INSUFFICIENT_EVIDENCE"
    assert "total<200" in result.reasons

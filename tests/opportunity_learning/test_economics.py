from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.opportunity_learning.contracts import EvidenceStatus, LearningTarget
from app.opportunity_learning.economics import selected_cost_sensitivity
from app.opportunity_learning.evaluation import evaluate_challenger
from tests.opportunity_learning.test_challengers import examples


class SelectLoss:
    target = LearningTarget.ONE_R_BEFORE_STOP
    def predict(self, features):
        return SimpleNamespace(evidence_status=EvidenceStatus.SUFFICIENT,
            probability=.9 if features.symbol == "S0" else .1)


def test_unselected_winners_cannot_inflate_selected_expectancy():
    data = examples()
    metrics = evaluate_challenger(SelectLoss(), data)
    assert metrics.average_r == -1
    report = selected_cost_sensitivity(SelectLoss(), data)
    assert report["scenarios"][0]["sample_size"] == 1
    assert report["scenarios"][1]["mean_net_r"] == -1.1
    assert report["scenarios"][1]["approximate_mean_lower_bound_r"] is None
    assert not report["promotion_authorized"]


def test_cost_stress_rejects_nonfinite_or_negative_costs():
    for cost in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            selected_cost_sensitivity(SelectLoss(), (), costs=(cost,))

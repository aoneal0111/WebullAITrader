import hashlib
import json
from decimal import Decimal

import pytest

from app.learning import LearningEngine, LearningPolicy
from tests.learning.helpers import learning_request, outcome


def test_statistics_and_deterministic_id():
    request = learning_request(outcomes=(outcome("10", ".10"), outcome("20", ".20"),
                                         outcome("-6", "-.06"), outcome("0", "0")))
    report = LearningEngine().analyze(request)
    assert (report.sample_size, report.wins, report.losses) == (4, 2, 1)
    assert report.win_rate == Decimal("0.5")
    assert report.average_win == Decimal("15")
    assert report.average_loss == Decimal("-6")
    assert report.total_profit == Decimal("30")
    assert report.total_loss == Decimal("-6")
    assert report.net_profit == Decimal("24")
    assert report.expectancy == Decimal("6")
    assert report.profit_factor == Decimal("5")
    assert report.largest_win == Decimal("20")
    assert report.largest_loss == Decimal("-6")
    assert report.average_return == Decimal(".06")
    canonical=json.dumps({"sample_size":4,"policy_version":"learning_policy_v1","net_profit":"24",
        "engine_version":"learning_engine_v1"},sort_keys=True,separators=(",",":"))
    assert report.report_id == hashlib.sha256(canonical.encode()).hexdigest()
    assert [item.name for item in report.checks] == ["sample not empty", "all outcomes closed", "valid pnl values"]
    assert all(item.passed for item in report.checks)


def test_no_losses_has_infinite_profit_factor_and_zero_loss_statistics():
    report = LearningEngine().analyze(learning_request(outcomes=(outcome("2"),)))
    assert report.profit_factor == Decimal("Infinity")
    assert report.average_loss == report.total_loss == report.largest_loss == 0


def test_minimum_sample_size_enforced():
    with pytest.raises(ValueError, match="minimum_sample_size"):
        LearningEngine().analyze(learning_request(outcomes=(outcome(),), policy=LearningPolicy(minimum_sample_size=2)))


def test_identical_input_produces_identical_report_and_does_not_mutate_outcomes():
    request = learning_request()
    before = tuple(item.to_dict() for item in request.outcomes)
    assert LearningEngine().analyze(request) == LearningEngine().analyze(request)
    assert tuple(item.to_dict() for item in request.outcomes) == before


def test_engine_requires_request():
    with pytest.raises(ValueError):
        LearningEngine().analyze([])

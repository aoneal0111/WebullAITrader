from dataclasses import FrozenInstanceError
import json
from types import MappingProxyType

import pytest

from app.learning import LearningReport, LearningRequest
from tests.learning.helpers import learning_request


def test_request_is_frozen_immutable_and_round_trips():
    request = learning_request(metadata={"nested": [1]})
    assert isinstance(request.outcomes, tuple)
    assert isinstance(request.metadata, MappingProxyType)
    assert LearningRequest.from_dict(request.to_dict()) == request
    json.dumps(request.to_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        request.outcomes = ()


@pytest.mark.parametrize("outcomes", [(), (object(),), "bad"])
def test_request_rejects_invalid_outcomes(outcomes):
    with pytest.raises(ValueError):
        learning_request(outcomes=outcomes)


def test_report_is_frozen_and_json_round_trips():
    from app.learning import LearningEngine
    report = LearningEngine().analyze(learning_request())
    assert LearningReport.from_dict(report.to_dict()) == report
    json.dumps(report.to_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        report.wins = 0

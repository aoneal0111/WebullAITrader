from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Decimal
import json
from types import MappingProxyType

import pytest

from app.outcomes import OutcomeRequest, TradeOutcome
from tests.outcomes.helpers import STAMP, outcome_request


def test_request_frozen_immutable_and_round_trip():
    request = outcome_request(metadata={"nested": [1]})
    assert isinstance(request.metadata, MappingProxyType)
    assert OutcomeRequest.from_dict(request.to_dict()) == request
    json.dumps(request.to_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        request.exit_price = Decimal("1")


@pytest.mark.parametrize("changes", [{"execution_result": object()}, {"exit_price": 0},
    {"exit_price": Decimal("NaN")}, {"timestamp": datetime(2026, 1, 1)},
    {"timestamp": STAMP - timedelta(days=1)}, {"policy": object()}])
def test_request_validation(changes):
    with pytest.raises(ValueError):
        outcome_request(**changes)


def test_outcome_frozen_json_round_trip():
    from app.outcomes import OutcomeRecorder
    outcome = OutcomeRecorder().record(outcome_request())
    assert TradeOutcome.from_dict(outcome.to_dict()) == outcome
    json.dumps(outcome.to_dict(), allow_nan=False)
    with pytest.raises(FrozenInstanceError):
        outcome.realized_pnl = Decimal("0")

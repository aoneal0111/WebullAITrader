from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Decimal
import json
from types import MappingProxyType
import pytest
from app.execution import ExecutionResult, PaperExecutionRequest
from tests.execution.helpers import STAMP, execution_request, proposal

def test_request_is_frozen_and_round_trips():
    item=execution_request(metadata={"x":[1]})
    assert isinstance(item.metadata,MappingProxyType)
    assert PaperExecutionRequest.from_dict(item.to_dict())==item
    json.dumps(item.to_dict(),allow_nan=False)
    with pytest.raises(FrozenInstanceError): item.timestamp=STAMP

@pytest.mark.parametrize("changes",[{"proposal":object()},{"timestamp":datetime(2026,1,1)},
    {"timestamp":STAMP-timedelta(minutes=5)},{"policy":object()}])
def test_request_validation(changes):
    with pytest.raises(ValueError): execution_request(**changes)

def test_result_is_frozen_json_round_trip():
    from app.execution import PaperExecutionEngine
    result=PaperExecutionEngine().execute(execution_request())
    assert ExecutionResult.from_dict(result.to_dict())==result
    json.dumps(result.to_dict(),allow_nan=False)
    with pytest.raises(FrozenInstanceError): result.fill_price=Decimal("1")

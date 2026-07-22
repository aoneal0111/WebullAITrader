from dataclasses import FrozenInstanceError
import pytest
from app.order_cancellation import *
from tests.order_cancellation.fixtures import request
from tests.order_cancellation.helpers import acknowledgement
def test_request_frozen_slotted_roundtrip():
 value=request();assert not hasattr(value,"__dict__") and OrderCancellationRequest.from_dict(value.to_dict())==value
 with pytest.raises(FrozenInstanceError):value.request_id="x"
 with pytest.raises(TypeError):value.metadata["x"]=1
def test_acknowledgement_roundtrip_and_bool_validation():
 value=acknowledgement();assert BrokerOrderCancellationAcknowledgement.from_dict(value.to_dict())==value
 with pytest.raises(OrderCancellationAcknowledgementError):BrokerOrderCancellationAcknowledgement("b",None,1,"bad")
def test_secret_metadata_rejected():
 with pytest.raises(OrderCancellationValidationError):OrderCancellationRequest("r","s","a","b",metadata={"access_token":"redacted"})
def test_result_roundtrip_and_consistency():
 result=OrderCancellationResult("r","b","c",OrderCancellationDecision.SUCCESS,CancellationAcknowledgementState.CANCELED,"canceled",(OrderCancellationCriteriaResult("ok",True,"passed"),));assert result.success and OrderCancellationResult.from_dict(result.to_dict())==result
 with pytest.raises(OrderCancellationValidationError):OrderCancellationResult("r","b",None,OrderCancellationDecision.SUCCESS,CancellationAcknowledgementState.REJECTED,"bad",())

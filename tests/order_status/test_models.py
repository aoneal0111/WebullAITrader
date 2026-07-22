from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal
import pytest
from app.order_status import *
from tests.order_status.fixtures import request
from tests.order_status.helpers import status
def test_request_frozen_slotted_roundtrip_optional_client():
 value=request();assert not hasattr(value,"__dict__") and OrderStatusRequest.from_dict(value.to_dict())==value and OrderStatusRequest.from_dict(request(None).to_dict())==request(None)
 with pytest.raises(FrozenInstanceError):value.request_id="x"
 with pytest.raises(TypeError):value.metadata["x"]=1
def test_snapshot_roundtrip_decimal_and_aware_observation():
 value=status();assert isinstance(value.requested_quantity,Decimal) and isinstance(value.observed_at,datetime) and BrokerOrderStatusSnapshot.from_dict(value.to_dict())==value
@pytest.mark.parametrize("state,filled,remaining,price",[(NormalizedOrderStatus.PARTIALLY_FILLED,"2","8","10.5"),(NormalizedOrderStatus.FILLED,"10","0","10.5"),(NormalizedOrderStatus.REJECTED,"0","10",None),(NormalizedOrderStatus.CANCELED,"0","10",None)])
def test_normalized_status_snapshots(state,filled,remaining,price):assert status(state,filled,remaining,price,"declined" if state is NormalizedOrderStatus.REJECTED else None).status is state
@pytest.mark.parametrize("filled,remaining",[("11","-1"),("2","7")])
def test_invalid_quantity_relationships(filled,remaining):
 with pytest.raises(OrderStatusSnapshotError):status(NormalizedOrderStatus.SUBMITTED,filled,remaining,"10" if Decimal(filled)>0 else None)
def test_status_specific_quantity_rules():
 with pytest.raises(OrderStatusSnapshotError):status(NormalizedOrderStatus.FILLED,"9","1","10")
 with pytest.raises(OrderStatusSnapshotError):status(NormalizedOrderStatus.PARTIALLY_FILLED,"0","10")
def test_secret_metadata_rejected():
 with pytest.raises(OrderStatusValidationError):OrderStatusRequest("r","s","a","b",metadata={"access_token":"redacted"})
def test_result_roundtrip_and_failure_snapshot_rule():
 result=OrderStatusResult("r","b","c",OrderStatusDecision.SUCCESS,status(),(OrderStatusCriteriaResult("ok",True,"passed"),));assert result.success and OrderStatusResult.from_dict(result.to_dict())==result
 with pytest.raises(OrderStatusValidationError):OrderStatusResult("r","b","c",OrderStatusDecision.GATEWAY_FAILURE,status(),())

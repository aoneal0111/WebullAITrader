from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.open_orders import *
from tests.open_orders.fixtures import request
from tests.open_orders.helpers import order,orders
def test_request_frozen_slotted_roundtrip():
 value=request();assert not hasattr(value,"__dict__") and OpenOrdersRequest.from_dict(value.to_dict())==value
 with pytest.raises(FrozenInstanceError):value.account_id="x"
 with pytest.raises(TypeError):value.metadata["x"]=1
def test_snapshot_normalized_decimal_roundtrip():
 value=order();assert value.symbol=="AAPL" and isinstance(value.requested_quantity,Decimal) and OpenOrderSnapshot.from_dict(value.to_dict())==value
@pytest.mark.parametrize("status",[NormalizedOrderStatus.FILLED,NormalizedOrderStatus.CANCELED,NormalizedOrderStatus.REJECTED,NormalizedOrderStatus.EXPIRED])
def test_terminal_status_rejected(status):
 with pytest.raises(OpenOrdersSnapshotError):order(status=status)
def test_quantity_and_price_validation():
 with pytest.raises(OpenOrdersSnapshotError):OpenOrderSnapshot("b","c","a","AAPL",OrderSide.BUY,OrderType.LIMIT,NormalizedOrderStatus.SUBMITTED,1,2,1)
 with pytest.raises(OpenOrdersSnapshotError):OpenOrderSnapshot("b","c","a","AAPL",OrderSide.BUY,OrderType.LIMIT,NormalizedOrderStatus.SUBMITTED,1,1,None)
def test_partial_fill_validation():
 value=OpenOrderSnapshot("b","c","a","AAPL",OrderSide.BUY,OrderType.LIMIT,NormalizedOrderStatus.PARTIALLY_FILLED,10,5,1,None,1);assert value.remaining_quantity==5
 with pytest.raises(OpenOrdersSnapshotError):OpenOrderSnapshot("b","c","a","AAPL",OrderSide.BUY,OrderType.LIMIT,NormalizedOrderStatus.PARTIALLY_FILLED,10,10,1)
def test_result_roundtrip_empty_success_and_failure_rule():
 result=OpenOrdersResult("r","a",OpenOrdersDecision.SUCCESS,orders(),(OpenOrdersCriteriaResult("ok",True,"passed"),));assert result.success and OpenOrdersResult.from_dict(result.to_dict())==result
 assert OpenOrdersResult("r","a",OpenOrdersDecision.SUCCESS,(),()).success
 with pytest.raises(OpenOrdersValidationError):OpenOrdersResult("r","a",OpenOrdersDecision.GATEWAY_FAILURE,orders(),())

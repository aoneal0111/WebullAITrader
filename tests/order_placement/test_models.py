from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.order_placement import *
from tests.order_placement.fixtures import order,request
from tests.order_placement.helpers import acknowledgement
def test_order_normalized_frozen_roundtrip():
 value=order();assert value.symbol=="AAPL" and isinstance(value.quantity,Decimal) and not hasattr(value,"__dict__") and OrderRequestModel.from_dict(value.to_dict())==value
 with pytest.raises(FrozenInstanceError):value.symbol="X"
 with pytest.raises(TypeError):value.metadata["x"]=1
def test_request_and_acknowledgement_roundtrip():assert OrderPlacementRequest.from_dict(request().to_dict())==request() and BrokerOrderAcknowledgement.from_dict(acknowledgement().to_dict())==acknowledgement()
@pytest.mark.parametrize("kind,limit,stop",[(OrderType.MARKET,None,None),(OrderType.LIMIT,"1",None),(OrderType.STOP,None,"1"),(OrderType.STOP_LIMIT,"1","2")])
def test_valid_order_type_price_rules(kind,limit,stop):assert order(kind,limit,stop).order_type is kind
@pytest.mark.parametrize("kind,limit,stop",[(OrderType.MARKET,"1",None),(OrderType.LIMIT,None,None),(OrderType.STOP,None,None),(OrderType.STOP_LIMIT,"1",None)])
def test_invalid_order_type_price_rules(kind,limit,stop):
 with pytest.raises(OrderPlacementValidationError):order(kind,limit,stop)
@pytest.mark.parametrize("quantity",[0,-1,"NaN",True])
def test_invalid_quantity(quantity):
 with pytest.raises(OrderPlacementValidationError):OrderRequestModel("r","a","AAPL",OrderSide.BUY,OrderType.MARKET,quantity,None,None,TimeInForce.DAY,"c")
def test_result_roundtrip_and_failure_cannot_expose_broker_id():
 result=OrderPlacementResult("r","c","b",AcknowledgementState.ACCEPTED,NormalizedOrderStatus.SUBMITTED,OrderPlacementDecision.SUCCESS,"accepted",(OrderPlacementCriteriaResult("ok",True,"passed"),));assert result.success and OrderPlacementResult.from_dict(result.to_dict())==result
 with pytest.raises(OrderPlacementValidationError):OrderPlacementResult("r","c","b",AcknowledgementState.REJECTED,NormalizedOrderStatus.REJECTED,OrderPlacementDecision.ORDER_REJECTED,"no",())

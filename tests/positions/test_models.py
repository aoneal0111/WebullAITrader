from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.positions import *
from tests.positions.helpers import positions
from tests.positions.fixtures import request
def test_request_frozen_slotted_roundtrip():
 r=request();assert not hasattr(r,"__dict__") and PositionsRequest.from_dict(r.to_dict())==r
 with pytest.raises(FrozenInstanceError):r.request_id="x"
 with pytest.raises(TypeError):r.metadata["x"]=1
def test_position_normalization_decimal_optional_and_roundtrip():
 p=positions()[0];assert p.symbol=="AAPL" and p.currency=="USD" and isinstance(p.quantity,Decimal) and p.realized_gain_loss is None;assert PositionModel.from_dict(p.to_dict())==p
def test_signed_short_and_gain_values_allowed():assert positions()[1].quantity<0 and positions()[1].market_value<0
@pytest.mark.parametrize("field,value",[("quantity",0),("quantity","NaN"),("average_cost",-1),("market_value",True)])
def test_invalid_position_values(field,value):
 data={"account_id":"a","symbol":"AAPL","asset_type":"EQUITY","quantity":1,"average_cost":1,"market_value":1,"unrealized_gain_loss":0,"realized_gain_loss":None,"currency":"USD"};data[field]=value
 with pytest.raises(PositionsValidationError):PositionModel(**data)
def test_result_roundtrip_and_failure_cannot_expose_positions():
 result=PositionsResult("r","s",PositionsDecision.SUCCESS,positions(),(PositionsCriteriaResult("ok",True,"passed"),));assert result.success and PositionsResult.from_dict(result.to_dict())==result
 with pytest.raises(PositionsValidationError):PositionsResult("r","s",PositionsDecision.GATEWAY_FAILURE,positions(),())

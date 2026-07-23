from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
import json,pytest
from app.webull_gateway import *
from tests.webull_gateway.helpers import STAMP
def test_auth_models_roundtrip_deterministic():
 r=LoginRequest(STAMP,"production-live",{"x":[1]});assert r==LoginRequest.from_dict(r.to_dict()) and r.request_id==LoginRequest(STAMP,"production-live",{"x":[1]}).request_id
 x=LoginResponse(True,STAMP,STAMP+timedelta(minutes=1),"production-live");assert LoginResponse.from_dict(x.to_dict())==x
 with pytest.raises(FrozenInstanceError):r.environment="x"
def test_business_response_roundtrips():
 a=AccountResponse(100,50,150,{"AAPL":2},-1,STAMP,"production-live");assert AccountResponse.from_dict(a.to_dict())==a;json.dumps(a.to_dict(),allow_nan=False)
 c=CancelOrderResponse(True,"opaque",STAMP,False);assert CancelOrderResponse.from_dict(c.to_dict())==c
 s=OrderStatusResponse("opaque",NormalizedOrderStatus.FILLED,2,2,Decimal("10"),STAMP);assert OrderStatusResponse.from_dict(s.to_dict())==s

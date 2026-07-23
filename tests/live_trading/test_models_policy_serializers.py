from dataclasses import FrozenInstanceError,replace
import pytest
from app.broker import serialize_broker_order_request
from app.live_trading import *
from app.research_portfolio import serialize_request as serialize_research_request
from tests.live_trading.helpers import request
def test_policy_defaults_enums_and_models_are_immutable():
    assert LiveTradingPolicy()==LiveTradingPolicy(enabled=False,fail_fast=True)
    assert LiveTradingStatus.COMPLETED.value=="completed" and LiveTradingOrderStatus.SKIPPED.value=="skipped"
    req=request()
    for model,field,value in ((req.identity,"live_trading_id","x"),(req.orders[0].identity,"order_entry_id","x"),(req.orders[0],"identity",req.orders[0].identity),(req,"orders",())):
        with pytest.raises(FrozenInstanceError):setattr(model,field,value)
def test_exact_nested_objects_and_order_are_preserved():
    req=request(3)
    assert req.research_request is req.research_request and isinstance(req.orders,tuple)
    assert all(req.orders[i].broker_request is req.orders[i].broker_request for i in range(3))
    assert [x.identity.order_entry_id for x in req.orders]==[f"order-entry-{i}" for i in range(3)]
def test_serialization_delegates_and_is_deterministic():
    req=request(2);data=serialize_request(req)
    assert data==serialize_request(req)==req.to_dict()
    assert data["research_request"]==serialize_research_request(req.research_request)
    assert data["orders"][0]["broker_request"]==serialize_broker_order_request(req.orders[0].broker_request)
    assert data["requested_at"]==req.requested_at.isoformat()
def test_serializers_reject_unsupported_types():
    serializers=(serialize_identity,serialize_order_identity,serialize_policy,serialize_order_request,serialize_request,serialize_criteria,serialize_research_record,serialize_order_record,serialize_summary,serialize_result)
    for serializer in serializers:
        with pytest.raises(LiveTradingSerializationError):serializer({})
def test_invalid_model_members_are_rejected():
    req=request(1)
    with pytest.raises(LiveTradingValidationError):LiveTradingIdentity("")
    with pytest.raises(LiveTradingValidationError):LiveTradingPolicy(enabled=1)
    with pytest.raises(LiveTradingValidationError):replace(req,orders=[])
    with pytest.raises(LiveTradingValidationError):replace(req.orders[0],broker_request=object())

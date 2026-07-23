import pytest
from app.broker import *
from app.order_placement import DeterministicOrderPlacementRuntime,OrderPlacementSerializationError
from tests.order_placement.fixtures import enabled_policy,request
from tests.order_placement.helpers import FakeGateway,FakeSessionManager

def test_request_and_result_serialization_match_order_placement():
    req=request();result=DeterministicOrderPlacementRuntime(FakeSessionManager(),FakeGateway(),enabled_policy()).place_order(req)
    assert serialize_broker_order_request(req)==req.to_dict()
    assert serialize_broker_order_result(result)==result.to_dict()

def test_serializers_reject_wrong_types_with_public_exception():
    for serializer in (serialize_broker_order_request,serialize_broker_order_result):
        with pytest.raises(OrderPlacementSerializationError):serializer({})

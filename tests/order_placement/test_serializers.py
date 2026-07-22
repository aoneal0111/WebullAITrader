import pytest
from app.order_placement import *
from tests.order_placement.fixtures import order,request
from tests.order_placement.helpers import acknowledgement
def test_serializers():assert serialize_order(order())==order().to_dict() and serialize_request(request())==request().to_dict() and serialize_acknowledgement(acknowledgement())==acknowledgement().to_dict() and serialize_policy(OrderPlacementPolicy())==OrderPlacementPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(OrderPlacementSerializationError):serialize_request(object())

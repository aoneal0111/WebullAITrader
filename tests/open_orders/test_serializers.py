import pytest
from app.open_orders import *
from tests.open_orders.fixtures import request
from tests.open_orders.helpers import order
def test_serializers():assert serialize_request(request())==request().to_dict() and serialize_snapshot(order())==order().to_dict() and serialize_policy(OpenOrdersPolicy())==OpenOrdersPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(OpenOrdersSerializationError):serialize_request(object())

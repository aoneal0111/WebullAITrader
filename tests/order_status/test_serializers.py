import pytest
from app.order_status import *
from tests.order_status.fixtures import request
from tests.order_status.helpers import status
def test_serializers():assert serialize_request(request())==request().to_dict() and serialize_snapshot(status())==status().to_dict() and serialize_policy(OrderStatusPolicy())==OrderStatusPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(OrderStatusSerializationError):serialize_request(object())

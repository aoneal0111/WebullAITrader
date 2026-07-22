import pytest
from app.order_cancellation import *
from tests.order_cancellation.fixtures import request
from tests.order_cancellation.helpers import acknowledgement
def test_serializers():assert serialize_request(request())==request().to_dict() and serialize_acknowledgement(acknowledgement())==acknowledgement().to_dict() and serialize_policy(OrderCancellationPolicy())==OrderCancellationPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(OrderCancellationSerializationError):serialize_request(object())

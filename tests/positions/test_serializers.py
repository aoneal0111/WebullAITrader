import pytest
from app.positions import *
from tests.positions.fixtures import request
from tests.positions.helpers import positions
def test_serializers():assert serialize_request(request())==request().to_dict() and serialize_position(positions()[0])==positions()[0].to_dict() and serialize_policy(PositionsPolicy())==PositionsPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(PositionsSerializationError):serialize_request(object())

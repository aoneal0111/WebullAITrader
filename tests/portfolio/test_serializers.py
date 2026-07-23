import pytest
from app.portfolio import *
from tests.portfolio.fixtures import request
def test_serializers():assert serialize_request(request())==request().to_dict() and serialize_policy(PortfolioPolicy())==PortfolioPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(PortfolioSerializationError):serialize_result(object())

import pytest
from app.market_data import *
from tests.market_data.fixtures import request
from tests.market_data.helpers import quotes
def test_serializers():assert serialize_request(request())==request().to_dict() and serialize_quote(quotes()[0])==quotes()[0].to_dict() and serialize_policy(MarketDataPolicy())==MarketDataPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(MarketDataSerializationError):serialize_request(object())

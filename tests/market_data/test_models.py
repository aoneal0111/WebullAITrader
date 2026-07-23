from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.market_data import *
from tests.market_data.fixtures import request
from tests.market_data.helpers import quotes
def test_request_normalized_frozen_roundtrip():
 r=request();assert r.symbols==("AAPL","MSFT") and not hasattr(r,"__dict__") and MarketDataRequest.from_dict(r.to_dict())==r
 with pytest.raises(FrozenInstanceError):r.request_id="x"
 with pytest.raises(TypeError):r.metadata["x"]=1
def test_quote_decimal_normalization_optional_fields_roundtrip():
 q=quotes()[0];assert q.symbol=="AAPL" and q.currency=="USD" and isinstance(q.last_price,Decimal);assert QuoteModel.from_dict(q.to_dict())==q
def test_optional_prices_and_volume():
 q=quotes()[1];assert q.bid_price is None and q.volume==0
@pytest.mark.parametrize("kwargs",[{"last_price":0},{"last_price":"NaN"},{"volume":-1},{"volume":True},{"bid_price":2,"ask_price":1},{"low_price":2,"high_price":1}])
def test_invalid_quote(kwargs):
 data={"symbol":"AAPL","asset_type":"EQUITY","last_price":1};data.update(kwargs)
 with pytest.raises(MarketDataValidationError):QuoteModel(**data)
def test_duplicate_request_and_result_symbols_rejected():
 with pytest.raises(MarketDataValidationError):MarketDataRequest("r","s",("AAPL","aapl"))
 q=quotes()[0]
 with pytest.raises(MarketDataValidationError):MarketDataResult("r","s",MarketDataDecision.SUCCESS,(q,q),())
def test_result_roundtrip_failure_no_quotes():
 result=MarketDataResult("r","s",MarketDataDecision.SUCCESS,quotes(),(MarketDataCriteriaResult("ok",True,"passed"),));assert result.success and MarketDataResult.from_dict(result.to_dict())==result
 with pytest.raises(MarketDataValidationError):MarketDataResult("r","s",MarketDataDecision.GATEWAY_FAILURE,quotes(),())

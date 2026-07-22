from app.market_data.exceptions import MarketDataSerializationError
from app.market_data.models import MarketDataCriteriaResult,MarketDataRequest,MarketDataResult,QuoteModel
from app.market_data.policies import MarketDataPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise MarketDataSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_quote(value):return _serialize(value,QuoteModel)
def serialize_request(value):return _serialize(value,MarketDataRequest)
def serialize_criteria(value):return _serialize(value,MarketDataCriteriaResult)
def serialize_result(value):return _serialize(value,MarketDataResult)
def serialize_policy(value):return _serialize(value,MarketDataPolicy)

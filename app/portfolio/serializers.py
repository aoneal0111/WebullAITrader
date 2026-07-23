from app.portfolio.exceptions import PortfolioSerializationError
from app.portfolio.models import PortfolioCriteriaResult,PortfolioPosition,PortfolioRequest,PortfolioResult,PortfolioSnapshot
from app.portfolio.policies import PortfolioPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise PortfolioSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_request(value):return _serialize(value,PortfolioRequest)
def serialize_position(value):return _serialize(value,PortfolioPosition)
def serialize_snapshot(value):return _serialize(value,PortfolioSnapshot)
def serialize_criteria(value):return _serialize(value,PortfolioCriteriaResult)
def serialize_result(value):return _serialize(value,PortfolioResult)
def serialize_policy(value):return _serialize(value,PortfolioPolicy)

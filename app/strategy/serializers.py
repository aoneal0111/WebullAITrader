from app.strategy.exceptions import StrategySerializationError
from app.strategy.models import StrategyContext,StrategyDecision,StrategyResult
from app.strategy.policies import StrategyPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise StrategySerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_context(value):return _serialize(value,StrategyContext)
def serialize_decision(value):return _serialize(value,StrategyDecision)
def serialize_result(value):return _serialize(value,StrategyResult)
def serialize_policy(value):return _serialize(value,StrategyPolicy)

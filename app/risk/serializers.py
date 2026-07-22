from app.risk.exceptions import RiskRuntimeSerializationError
from app.risk.models import RiskContext,RiskCriteriaResult,RiskResult
def _serialize(value,expected):
 if not isinstance(value,expected):raise RiskRuntimeSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_context(value):return _serialize(value,RiskContext)
def serialize_criteria(value):return _serialize(value,RiskCriteriaResult)
def serialize_result(value):return _serialize(value,RiskResult)

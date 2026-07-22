from app.positions.exceptions import PositionsSerializationError
from app.positions.models import PositionModel,PositionsCriteriaResult,PositionsRequest,PositionsResult
from app.positions.policies import PositionsPolicy
def _serialize(value,expected):
    if not isinstance(value,expected):raise PositionsSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
def serialize_position(value):return _serialize(value,PositionModel)
def serialize_request(value):return _serialize(value,PositionsRequest)
def serialize_criteria(value):return _serialize(value,PositionsCriteriaResult)
def serialize_result(value):return _serialize(value,PositionsResult)
def serialize_policy(value):return _serialize(value,PositionsPolicy)

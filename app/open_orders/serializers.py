from app.open_orders.exceptions import OpenOrdersSerializationError
from app.open_orders.models import OpenOrderSnapshot,OpenOrdersCriteriaResult,OpenOrdersRequest,OpenOrdersResult
from app.open_orders.policies import OpenOrdersPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise OpenOrdersSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_request(value):return _serialize(value,OpenOrdersRequest)
def serialize_snapshot(value):return _serialize(value,OpenOrderSnapshot)
def serialize_criteria(value):return _serialize(value,OpenOrdersCriteriaResult)
def serialize_result(value):return _serialize(value,OpenOrdersResult)
def serialize_policy(value):return _serialize(value,OpenOrdersPolicy)

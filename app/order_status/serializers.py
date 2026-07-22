from app.order_status.exceptions import OrderStatusSerializationError
from app.order_status.models import BrokerOrderStatusSnapshot,OrderStatusCriteriaResult,OrderStatusRequest,OrderStatusResult
from app.order_status.policies import OrderStatusPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise OrderStatusSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_request(value):return _serialize(value,OrderStatusRequest)
def serialize_snapshot(value):return _serialize(value,BrokerOrderStatusSnapshot)
def serialize_criteria(value):return _serialize(value,OrderStatusCriteriaResult)
def serialize_result(value):return _serialize(value,OrderStatusResult)
def serialize_policy(value):return _serialize(value,OrderStatusPolicy)

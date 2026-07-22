from app.order_cancellation.exceptions import OrderCancellationSerializationError
from app.order_cancellation.models import BrokerOrderCancellationAcknowledgement,OrderCancellationCriteriaResult,OrderCancellationRequest,OrderCancellationResult
from app.order_cancellation.policies import OrderCancellationPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise OrderCancellationSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_request(value):return _serialize(value,OrderCancellationRequest)
def serialize_acknowledgement(value):return _serialize(value,BrokerOrderCancellationAcknowledgement)
def serialize_criteria(value):return _serialize(value,OrderCancellationCriteriaResult)
def serialize_result(value):return _serialize(value,OrderCancellationResult)
def serialize_policy(value):return _serialize(value,OrderCancellationPolicy)

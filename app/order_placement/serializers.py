from app.order_placement.exceptions import OrderPlacementSerializationError
from app.order_placement.models import BrokerOrderAcknowledgement,OrderPlacementCriteriaResult,OrderPlacementRequest,OrderPlacementResult,OrderRequestModel
from app.order_placement.policies import OrderPlacementPolicy
def _serialize(value,expected):
 if not isinstance(value,expected):raise OrderPlacementSerializationError(f"value must be {expected.__name__}")
 return value.to_dict()
def serialize_order(value):return _serialize(value,OrderRequestModel)
def serialize_request(value):return _serialize(value,OrderPlacementRequest)
def serialize_acknowledgement(value):return _serialize(value,BrokerOrderAcknowledgement)
def serialize_criteria(value):return _serialize(value,OrderPlacementCriteriaResult)
def serialize_result(value):return _serialize(value,OrderPlacementResult)
def serialize_policy(value):return _serialize(value,OrderPlacementPolicy)

from app.order_placement.exceptions import OrderPlacementDependencyError,OrderPlacementValidationError
from app.order_placement.models import OrderPlacementRequest
from app.order_placement.policies import OrderPlacementPolicy
def validate_dependencies(session_manager,broker_gateway,policy):
 if session_manager is None or not callable(getattr(session_manager,"state",None)):raise OrderPlacementDependencyError("session manager must expose state()")
 if broker_gateway is None or not callable(getattr(broker_gateway,"place_order",None)):raise OrderPlacementDependencyError("broker order placement gateway must expose place_order(request)")
 if not isinstance(policy,OrderPlacementPolicy):raise OrderPlacementDependencyError("policy must be OrderPlacementPolicy")
def validate_request(request):
 if not isinstance(request,OrderPlacementRequest):raise OrderPlacementValidationError("request must be OrderPlacementRequest")
 return request

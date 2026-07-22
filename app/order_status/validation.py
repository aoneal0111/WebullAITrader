from app.order_status.exceptions import OrderStatusDependencyError,OrderStatusValidationError
from app.order_status.models import OrderStatusRequest
from app.order_status.policies import OrderStatusPolicy
def validate_dependencies(session_manager,broker_gateway,policy):
 if session_manager is None or not callable(getattr(session_manager,"state",None)):raise OrderStatusDependencyError("session manager must expose state()")
 if broker_gateway is None or not callable(getattr(broker_gateway,"get_order_status",None)):raise OrderStatusDependencyError("broker order status gateway must expose get_order_status(request)")
 if not isinstance(policy,OrderStatusPolicy):raise OrderStatusDependencyError("policy must be OrderStatusPolicy")
def validate_request(request):
 if not isinstance(request,OrderStatusRequest):raise OrderStatusValidationError("request must be OrderStatusRequest")
 return request

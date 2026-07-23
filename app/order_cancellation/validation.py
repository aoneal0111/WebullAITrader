from app.order_cancellation.exceptions import OrderCancellationDependencyError,OrderCancellationValidationError
from app.order_cancellation.models import OrderCancellationRequest
from app.order_cancellation.policies import OrderCancellationPolicy
def validate_dependencies(session_manager,broker_gateway,policy):
 if session_manager is None or not callable(getattr(session_manager,"state",None)):raise OrderCancellationDependencyError("session manager must expose state()")
 if broker_gateway is None or not callable(getattr(broker_gateway,"cancel_order",None)):raise OrderCancellationDependencyError("broker order cancellation gateway must expose cancel_order(request)")
 if not isinstance(policy,OrderCancellationPolicy):raise OrderCancellationDependencyError("policy must be OrderCancellationPolicy")
def validate_request(request):
 if not isinstance(request,OrderCancellationRequest):raise OrderCancellationValidationError("request must be OrderCancellationRequest")
 return request

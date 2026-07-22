from app.open_orders.exceptions import OpenOrdersDependencyError,OpenOrdersValidationError
from app.open_orders.models import OpenOrdersRequest
from app.open_orders.policies import OpenOrdersPolicy
def validate_dependencies(session_manager,broker_gateway,policy):
 if session_manager is None or not callable(getattr(session_manager,"state",None)):raise OpenOrdersDependencyError("session manager must expose state()")
 if broker_gateway is None or not callable(getattr(broker_gateway,"get_open_orders",None)):raise OpenOrdersDependencyError("broker open orders gateway must expose get_open_orders(request)")
 if not isinstance(policy,OpenOrdersPolicy):raise OpenOrdersDependencyError("policy must be OpenOrdersPolicy")
def validate_request(request):
 if not isinstance(request,OpenOrdersRequest):raise OpenOrdersValidationError("request must be OpenOrdersRequest")
 return request

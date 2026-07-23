from app.positions.exceptions import PositionsDependencyError, PositionsValidationError
from app.positions.models import PositionsRequest
from app.positions.policies import PositionsPolicy


def validate_dependencies(session_manager, broker_gateway, policy):
    if session_manager is None or not callable(getattr(session_manager,"state",None)):raise PositionsDependencyError("session manager must expose state()")
    if broker_gateway is None or not callable(getattr(broker_gateway,"get_positions",None)):raise PositionsDependencyError("broker position gateway must expose get_positions(request)")
    if not isinstance(policy,PositionsPolicy):raise PositionsDependencyError("policy must be PositionsPolicy")


def validate_request(request):
    if not isinstance(request,PositionsRequest):raise PositionsValidationError("request must be PositionsRequest")
    return request

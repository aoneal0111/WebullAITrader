from app.live_trading.exceptions import LiveTradingDependencyError,LiveTradingValidationError
from app.live_trading.models import LiveTradingRequest
def validate_dependencies(research_executor,broker_executor):
    if research_executor is None or isinstance(research_executor,type) or not callable(getattr(research_executor,"run",None)):raise LiveTradingDependencyError("research portfolio executor must be an instance exposing run(request)")
    if broker_executor is None or isinstance(broker_executor,type) or not callable(getattr(broker_executor,"place_order",None)):raise LiveTradingDependencyError("broker order executor must be an instance exposing place_order(request)")
def validate_request(request,minimal=False):
    if not isinstance(request,LiveTradingRequest):raise LiveTradingValidationError("request must be LiveTradingRequest")
    if minimal:return request
    errors=[];seen=set()
    for order in request.orders:
        value=order.identity.order_entry_id
        if value in seen:errors.append(f"duplicate order entry ID at order entry {value}")
        seen.add(value)
    return tuple(errors)

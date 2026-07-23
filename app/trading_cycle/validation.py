from app.trading_cycle.exceptions import TradingCycleDependencyError,TradingCycleValidationError
from app.trading_cycle.models import TradingCycleBuildRequest
from app.trading_cycle.policies import TradingCyclePolicy
def validate_dependencies(policy,evaluator):
    if not isinstance(policy,TradingCyclePolicy):raise TradingCycleDependencyError("policy must be TradingCyclePolicy")
    if evaluator is not None and not callable(getattr(evaluator,"evaluate",None)):raise TradingCycleDependencyError("metrics evaluator must expose evaluate(request)")
def validate_request(request):
    if not isinstance(request,TradingCycleBuildRequest):raise TradingCycleValidationError("request must be TradingCycleBuildRequest")
    return request

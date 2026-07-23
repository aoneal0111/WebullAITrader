from app.paper_trading.exceptions import PaperTradingDependencyError, PaperTradingValidationError
from app.paper_trading.milestone_models import PaperExecutionRequest
from app.paper_trading.policies import PaperTradingPolicy


def validate_dependencies(evaluator, policy):
    if evaluator is None or not callable(getattr(evaluator, "evaluate", None)):
        raise PaperTradingDependencyError("fill evaluator must expose evaluate(request, instruction, account, market_price, policy)")
    if not isinstance(policy, PaperTradingPolicy):
        raise PaperTradingDependencyError("policy must be PaperTradingPolicy")


def validate_request(request):
    if not isinstance(request, PaperExecutionRequest):
        raise PaperTradingValidationError("request must be PaperExecutionRequest")
    return request

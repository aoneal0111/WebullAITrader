from app.portfolio.exceptions import PortfolioDependencyError,PortfolioValidationError
from app.portfolio.models import PortfolioRequest
from app.portfolio.policies import PortfolioPolicy
def validate_dependencies(account_information_runtime,positions_runtime,policy):
 if account_information_runtime is None or not callable(getattr(account_information_runtime,"get_account_information",None)):raise PortfolioDependencyError("account information runtime must expose get_account_information(request)")
 if positions_runtime is None or not callable(getattr(positions_runtime,"get_positions",None)):raise PortfolioDependencyError("positions runtime must expose get_positions(request)")
 if not isinstance(policy,PortfolioPolicy):raise PortfolioDependencyError("policy must be PortfolioPolicy")
def validate_request(request):
 if not isinstance(request,PortfolioRequest):raise PortfolioValidationError("request must be PortfolioRequest")
 return request

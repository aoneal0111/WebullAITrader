from typing import Protocol
from app.portfolio.models import PortfolioRequest,PortfolioResult
class PortfolioRuntime(Protocol):
 def get_portfolio(self,request:PortfolioRequest)->PortfolioResult:...

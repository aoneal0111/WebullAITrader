from decimal import Decimal
from app.account_information.models import AccountInformationResult
from app.portfolio.models import *
from app.portfolio.validation import validate_dependencies,validate_request
from app.positions.models import PositionsResult
class DeterministicPortfolioRuntime:
 def __init__(self,account_information_runtime,positions_runtime,policy):validate_dependencies(account_information_runtime,positions_runtime,policy);self._account_information_runtime=account_information_runtime;self._positions_runtime=positions_runtime;self._policy=policy
 def get_portfolio(self,request):
  request=validate_request(request)
  if not self._policy.enabled:return self._result(request,PortfolioDecision.DISABLED,None,(False,False,False))
  try:account=self._account_information_runtime.get_account_information(request)
  except Exception:return self._result(request,PortfolioDecision.DEPENDENCY_FAILURE,None,(True,False,False))
  try:positions=self._positions_runtime.get_positions(request)
  except Exception:return self._result(request,PortfolioDecision.DEPENDENCY_FAILURE,None,(True,True,False))
  if not isinstance(account,AccountInformationResult) or not isinstance(positions,PositionsResult) or not account.success or not positions.success:return self._result(request,PortfolioDecision.DEPENDENCY_FAILURE,None,(True,True,False))
  if account.account_id!=request.account_id or any(p.account_id!=request.account_id for p in positions.positions):return self._result(request,PortfolioDecision.INVALID_ACCOUNT,None,(True,True,False))
  market_value=sum((p.market_value for p in positions.positions),Decimal("0"));total_value=account.cash_balance+market_value
  composed=tuple(PortfolioPosition(p.symbol,p.quantity,p.market_value,p.quantity*p.average_cost,p.unrealized_gain_loss,(p.market_value/market_value if market_value else Decimal("0")),p.metadata) for p in positions.positions)
  snapshot=PortfolioSnapshot(request.account_id,account.cash_balance,account.buying_power,account.equity,market_value,total_value,composed,{"currency":account.currency})
  return self._result(request,PortfolioDecision.SUCCESS,snapshot,(True,True,True))
 def _result(self,request,decision,snapshot,passed):
  names=("policy_enabled","dependencies_succeeded","account_valid");details=("portfolio policy enabled","account information and positions composed once","account identities matched")
  criteria=tuple(PortfolioCriteriaResult(n,p,d) for n,p,d in zip(names,passed,details))
  values=(snapshot.cash,snapshot.buying_power,snapshot.equity,snapshot.market_value,snapshot.total_value,snapshot.positions) if snapshot else (Decimal("0"),)*5+((),)
  return PortfolioResult(request.request_id,request.account_id,*values,decision,criteria,{"deterministic":True,"policy_version":self._policy.version})

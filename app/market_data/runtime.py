from app.market_data.exceptions import MarketDataDependencyError
from app.market_data.models import MarketDataCriteriaResult,MarketDataDecision,MarketDataResult,QuoteModel
from app.market_data.validation import validate_dependencies,validate_request
from app.session import SessionSnapshot,SessionStatus
class DeterministicMarketDataRuntime:
 def __init__(self,session_manager,broker_gateway,policy):validate_dependencies(session_manager,broker_gateway,policy);self._session_manager=session_manager;self._broker_gateway=broker_gateway;self._policy=policy
 def get_market_data(self,request):
  request=validate_request(request)
  if not self._policy.enabled:return self._result(request,MarketDataDecision.DISABLED,(),(False,False,False))
  try:snapshot=self._session_manager.state()
  except Exception as exc:raise MarketDataDependencyError("session manager failed to resolve session") from exc
  if not isinstance(snapshot,SessionSnapshot):raise MarketDataDependencyError("session manager returned invalid snapshot")
  valid=snapshot.status is SessionStatus.ACTIVE and snapshot.session is not None and snapshot.session.identifier.value==request.session_id
  if not valid:return self._result(request,MarketDataDecision.SESSION_INVALID,(),(True,False,False))
  try:quotes=self._broker_gateway.get_market_data(request)
  except Exception:return self._result(request,MarketDataDecision.GATEWAY_FAILURE,(),(True,True,False))
  if not isinstance(quotes,tuple) or any(not isinstance(x,QuoteModel) for x in quotes):raise MarketDataDependencyError("broker market data gateway returned invalid quotes")
  if tuple(x.symbol for x in quotes)!=request.symbols:raise MarketDataDependencyError("gateway quote symbols or order do not match request")
  return self._result(request,MarketDataDecision.SUCCESS,quotes,(True,True,True))
 def _result(self,request,decision,quotes,passed):
  names=("policy_enabled","session_active","gateway_succeeded");details=("market data policy enabled","matching active session resolved","broker market data gateway returned broker-neutral quotes")
  return MarketDataResult(request.request_id,request.session_id,decision,quotes,tuple(MarketDataCriteriaResult(n,p,d) for n,p,d in zip(names,passed,details)),{"deterministic":True,"policy_version":self._policy.version})

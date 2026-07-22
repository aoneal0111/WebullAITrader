from app.open_orders.exceptions import OpenOrdersDependencyError,OpenOrdersIdentityError,OpenOrdersSnapshotError
from app.open_orders.models import *
from app.open_orders.validation import validate_dependencies,validate_request
from app.session.models import SessionSnapshot,SessionStatus
class DeterministicOpenOrdersRuntime:
 def __init__(self,session_manager,broker_gateway,policy):validate_dependencies(session_manager,broker_gateway,policy);self._session_manager=session_manager;self._broker_gateway=broker_gateway;self._policy=policy
 def get_open_orders(self,request):
  request=validate_request(request)
  if not self._policy.enabled:return self._result(request,OpenOrdersDecision.DISABLED,(),(False,False,False))
  try:snapshot=self._session_manager.state()
  except Exception as exc:raise OpenOrdersDependencyError("session manager failed to resolve session") from exc
  if not isinstance(snapshot,SessionSnapshot):raise OpenOrdersDependencyError("session manager returned invalid snapshot")
  if snapshot.status is not SessionStatus.ACTIVE or snapshot.session is None:return self._result(request,OpenOrdersDecision.SESSION_INVALID,(),(True,False,False))
  try:orders=self._broker_gateway.get_open_orders(request)
  except Exception:return self._result(request,OpenOrdersDecision.GATEWAY_FAILURE,(),(True,True,False))
  if not isinstance(orders,tuple) or any(not isinstance(x,OpenOrderSnapshot) for x in orders):raise OpenOrdersDependencyError("broker gateway returned invalid open orders")
  if any(x.account_id!=request.account_id for x in orders):raise OpenOrdersIdentityError("open order account ID mismatch")
  broker_ids=tuple(x.broker_order_id for x in orders)
  if len(set(broker_ids))!=len(broker_ids):raise OpenOrdersSnapshotError("duplicate broker order IDs")
  client_ids=tuple(x.client_order_id for x in orders if x.client_order_id is not None)
  if len(set(client_ids))!=len(client_ids):raise OpenOrdersSnapshotError("duplicate client order IDs")
  return self._result(request,OpenOrdersDecision.SUCCESS,orders,(True,True,True))
 def _result(self,request,decision,orders,passed):
  names=("policy_enabled","session_active","orders_validated");details=("open orders policy enabled","active session resolved","one ordered gateway result validated")
  return OpenOrdersResult(request.request_id,request.account_id,decision,orders,tuple(OpenOrdersCriteriaResult(n,p,d) for n,p,d in zip(names,passed,details)),{"deterministic":True,"policy_version":self._policy.version})

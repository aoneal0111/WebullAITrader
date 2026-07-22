from app.order_placement.exceptions import OrderPlacementDependencyError
from app.order_placement.models import *
from app.order_placement.validation import validate_dependencies,validate_request
from app.session import SessionSnapshot,SessionStatus
class DeterministicOrderPlacementRuntime:
 def __init__(self,session_manager,broker_gateway,policy):validate_dependencies(session_manager,broker_gateway,policy);self._session_manager=session_manager;self._broker_gateway=broker_gateway;self._policy=policy
 def place_order(self,request):
  request=validate_request(request)
  if not self._policy.enabled:return self._result(request,OrderPlacementDecision.DISABLED,None,(False,False,False),"order placement disabled")
  try:snapshot=self._session_manager.state()
  except Exception as exc:raise OrderPlacementDependencyError("session manager failed to resolve session") from exc
  if not isinstance(snapshot,SessionSnapshot):raise OrderPlacementDependencyError("session manager returned invalid snapshot")
  valid=snapshot.status is SessionStatus.ACTIVE and snapshot.session is not None and snapshot.session.identifier.value==request.session_id
  if not valid:return self._result(request,OrderPlacementDecision.SESSION_INVALID,None,(True,False,False),"active session required")
  try:ack=self._broker_gateway.place_order(request)
  except Exception:return self._result(request,OrderPlacementDecision.GATEWAY_FAILURE,None,(True,True,False),"broker gateway failed")
  if not isinstance(ack,BrokerOrderAcknowledgement):raise OrderPlacementDependencyError("broker gateway returned invalid acknowledgement")
  if ack.client_order_id!=request.order.client_order_id:raise OrderPlacementDependencyError("acknowledgement client order ID mismatch")
  if not ack.accepted:return self._result(request,OrderPlacementDecision.ORDER_REJECTED,ack,(True,True,False),ack.message)
  return self._result(request,OrderPlacementDecision.SUCCESS,ack,(True,True,True),ack.message)
 def _result(self,request,decision,ack,passed,message):
  names=("policy_enabled","session_active","order_accepted");details=("order placement policy enabled","matching active session resolved","broker gateway accepted order")
  if decision is OrderPlacementDecision.SUCCESS:state=AcknowledgementState.ACCEPTED;status=ack.status;broker_id=ack.broker_order_id
  elif decision is OrderPlacementDecision.ORDER_REJECTED:state=AcknowledgementState.REJECTED;status=ack.status;broker_id=""
  elif decision is OrderPlacementDecision.GATEWAY_FAILURE:state=AcknowledgementState.FAILED;status=NormalizedOrderStatus.FAILED;broker_id=""
  else:state=AcknowledgementState.NOT_SENT;status=NormalizedOrderStatus.NOT_SUBMITTED;broker_id=""
  criteria=tuple(OrderPlacementCriteriaResult(n,p,d) for n,p,d in zip(names,passed,details))
  return OrderPlacementResult(request.order.request_id,request.order.client_order_id,broker_id,state,status,decision,message,criteria,{"deterministic":True,"policy_version":self._policy.version})

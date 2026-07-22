from app.order_cancellation.exceptions import OrderCancellationDependencyError,OrderCancellationIdentityError
from app.order_cancellation.models import *
from app.order_cancellation.validation import validate_dependencies,validate_request
from app.session.models import SessionSnapshot,SessionStatus
class DeterministicOrderCancellationRuntime:
 def __init__(self,session_manager,broker_gateway,policy):validate_dependencies(session_manager,broker_gateway,policy);self._session_manager=session_manager;self._broker_gateway=broker_gateway;self._policy=policy
 def cancel_order(self,request):
  request=validate_request(request)
  if not self._policy.enabled:return self._result(request,OrderCancellationDecision.DISABLED,None,(False,False,False),"order cancellation disabled")
  try:snapshot=self._session_manager.state()
  except Exception as exc:raise OrderCancellationDependencyError("session manager failed to resolve session") from exc
  if not isinstance(snapshot,SessionSnapshot):raise OrderCancellationDependencyError("session manager returned invalid snapshot")
  valid=snapshot.status is SessionStatus.ACTIVE and snapshot.session is not None and snapshot.session.identifier.value==request.session_id
  if not valid:return self._result(request,OrderCancellationDecision.SESSION_INVALID,None,(True,False,False),"matching active session required")
  try:ack=self._broker_gateway.cancel_order(request)
  except Exception:return self._result(request,OrderCancellationDecision.GATEWAY_FAILURE,None,(True,True,False),"broker gateway failed")
  if ack is None:return self._result(request,OrderCancellationDecision.ORDER_NOT_FOUND,None,(True,True,False),"order not found")
  if not isinstance(ack,BrokerOrderCancellationAcknowledgement):raise OrderCancellationDependencyError("broker gateway returned invalid cancellation acknowledgement")
  if ack.broker_order_id!=request.broker_order_id:raise OrderCancellationIdentityError("broker order ID mismatch")
  if request.client_order_id is not None and ack.client_order_id!=request.client_order_id:raise OrderCancellationIdentityError("client order ID mismatch")
  decision=OrderCancellationDecision.SUCCESS if ack.accepted else OrderCancellationDecision.CANCELLATION_REJECTED
  return self._result(request,decision,ack,(True,True,ack.accepted),ack.message)
 def _result(self,request,decision,ack,passed,message):
  states={OrderCancellationDecision.SUCCESS:CancellationAcknowledgementState.CANCELED,OrderCancellationDecision.CANCELLATION_REJECTED:CancellationAcknowledgementState.REJECTED,OrderCancellationDecision.ORDER_NOT_FOUND:CancellationAcknowledgementState.NOT_FOUND,OrderCancellationDecision.GATEWAY_FAILURE:CancellationAcknowledgementState.FAILED,OrderCancellationDecision.DISABLED:CancellationAcknowledgementState.NOT_SENT,OrderCancellationDecision.SESSION_INVALID:CancellationAcknowledgementState.NOT_SENT}
  names=("policy_enabled","session_active","cancellation_accepted");details=("order cancellation policy enabled","matching active session resolved","broker gateway accepted cancellation")
  return OrderCancellationResult(request.request_id,request.broker_order_id,request.client_order_id,decision,states[decision],message,tuple(OrderCancellationCriteriaResult(n,p,d) for n,p,d in zip(names,passed,details)),{"deterministic":True,"policy_version":self._policy.version})

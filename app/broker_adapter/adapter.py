import hashlib,json
from decimal import Decimal
from app.broker_adapter.mapping import BrokerOrderMapper,client_order_id
from app.broker_adapter.models import *
from app.broker_adapter.models_base import *
from app.broker_execution import ExecutionMode
from app.committee.models import thaw_json_value
from app.live_broker import LiveExecutionDecision
from app.trade_proposals.models import TradeDirection
class BrokerAdapter:
 name="broker_adapter_v1"
 def __init__(self,transport):
  if not hasattr(transport,"submit_order"):raise ValueError("transport must implement submit_order")
  self.transport=transport;self.mapper=BrokerOrderMapper()
 def execute(self,r:BrokerAdapterRequest):
  if not isinstance(r,BrokerAdapterRequest):raise ValueError("request must be BrokerAdapterRequest")
  i,p=r.invocation,r.policy;integral=i.quantity.is_finite() and i.quantity==i.quantity.to_integral_value();q=int(i.quantity) if integral and i.quantity>=0 else 0;side=BrokerOrderSide.BUY if i.direction is TradeDirection.LONG else BrokerOrderSide.SELL;price=i.entry_price if r.order_type is BrokerOrderType.LIMIT else Decimal("0");notional=i.entry_price*i.quantity
  checks=(p.adapter_enabled,(i.decision is LiveExecutionDecision.READY) or not p.require_ready_invocation,(i.mode is ExecutionMode.LIVE) or not p.require_live_mode,bool(i.symbol) and i.symbol==i.symbol.upper(),integral and q>0,r.order_type in p.allowed_order_types,r.time_in_force in p.allowed_time_in_force,r.order_type is BrokerOrderType.MARKET or price>0,q<=p.maximum_quantity,notional<=p.maximum_notional)
  reasons=(BrokerExecutionReason.ADAPTER_DISABLED,BrokerExecutionReason.INVOCATION_NOT_READY,BrokerExecutionReason.INVOCATION_NOT_LIVE,BrokerExecutionReason.INVALID_SYMBOL,BrokerExecutionReason.INVALID_QUANTITY,BrokerExecutionReason.ORDER_TYPE_NOT_ALLOWED,BrokerExecutionReason.TIME_IN_FORCE_NOT_ALLOWED,BrokerExecutionReason.INVALID_LIMIT_PRICE,BrokerExecutionReason.QUANTITY_EXCEEDS_LIMIT,BrokerExecutionReason.NOTIONAL_EXCEEDS_LIMIT)
  failed=next((x for x,v in enumerate(checks) if not v),None)
  cid=_placeholder(i,r) if failed is not None else client_order_id(i,r,side,q,price,self.name)
  if failed is None and p.reject_duplicate_client_order_ids and cid in r.state.submitted_client_order_ids:failed=len(reasons);reason=BrokerExecutionReason.DUPLICATE_CLIENT_ORDER_ID
  elif failed is not None:reason=reasons[failed]
  else:reason=None
  metadata={"deterministic":True,"adapter_enabled":p.adapter_enabled,"invocation_decision":i.decision.value,"invocation_reason":i.reason.value,"requested_order_type":r.order_type.value,"requested_time_in_force":r.time_in_force.value,"transport_invoked":False,"order_notional":str(notional),"policy_version":p.version,"adapter_version":self.name}
  metadata.update(thaw_json_value(r.metadata))
  if reason is not None:return self._result(r,cid,side,q,price,0,Decimal("0"),BrokerExecutionStatus.BLOCKED,reason,"","",False,r.timestamp,metadata)
  order=self.mapper.map(r);metadata["transport_invoked"]=True
  try:response=self.transport.submit_order(order)
  except Exception:return self._result(r,cid,side,q,price,0,Decimal("0"),BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.TRANSPORT_FAILURE,"","",True,r.timestamp,metadata)
  if not isinstance(response,BrokerTransportResponse):return self._result(r,cid,side,q,price,0,Decimal("0"),BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.TRANSPORT_FAILURE,"","",False,r.timestamp,metadata)
  if response.client_order_id!=cid:status,reason=BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.RESPONSE_CLIENT_ORDER_ID_MISMATCH
  elif response.timestamp<order.submitted_at:status,reason=BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.RESPONSE_TIMESTAMP_INVALID
  elif response.accepted_quantity>q:status,reason=BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.RESPONSE_QUANTITY_INVALID
  elif response.status is BrokerTransportStatus.ACCEPTED:status,reason=BrokerExecutionStatus.SUBMITTED,BrokerExecutionReason.SUBMITTED
  elif response.status is BrokerTransportStatus.REJECTED:status,reason=BrokerExecutionStatus.REJECTED,BrokerExecutionReason.TRANSPORT_REJECTED
  elif response.status is BrokerTransportStatus.FAILED:status,reason=BrokerExecutionStatus.TRANSPORT_FAILED,BrokerExecutionReason.TRANSPORT_FAILURE
  else:status,reason=BrokerExecutionStatus.UNKNOWN,BrokerExecutionReason.UNKNOWN_TRANSPORT_STATUS
  return self._result(r,cid,side,q,price,response.accepted_quantity,response.accepted_price,status,reason,response.transport_request_id,response.broker_order_reference,response.retryable,response.timestamp,metadata)
 def _result(self,r,cid,side,q,price,accepted,accepted_price,status,reason,tr,br,retryable,timestamp,metadata):
  i=r.invocation;rid=_result_id(cid,i,tr,br,timestamp,status,reason,accepted,accepted_price,r.policy.version,self.name)
  return BrokerLiveExecutionResult(rid,cid,i.invocation_id,i.authorization_id,i.proposal_id,i.request_fingerprint,i.symbol,side,q,accepted,r.order_type,price,accepted_price,r.time_in_force,i.environment,timestamp,status,reason,tr,br,retryable,r.policy.version,self.name,metadata)
def _placeholder(i,r):return hashlib.sha256(json.dumps({"invocation_id":i.invocation_id,"timestamp":r.timestamp.isoformat(),"policy_version":r.policy.version},sort_keys=True,separators=(",",":" )).encode()).hexdigest()
def _result_id(cid,i,tr,br,t,s,r,q,p,policy,adapter):
 x=json.dumps({"client_order_id":cid,"invocation_id":i.invocation_id,"authorization_id":i.authorization_id,"proposal_id":i.proposal_id,"transport_request_id":tr,"broker_order_reference":br,"timestamp":t.isoformat(),"status":s.value,"reason":r.value,"quantity_accepted":q,"accepted_price":str(p),"policy_version":policy,"adapter_version":adapter},sort_keys=True,separators=(",",":"));return hashlib.sha256(x.encode()).hexdigest()

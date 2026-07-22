import hashlib,json
from app.broker_adapter.models import BrokerOrderRequest
from app.broker_adapter.models_base import *
from app.live_broker import LiveExecutionDecision
from app.broker_execution import ExecutionMode
from app.trade_proposals.models import TradeDirection
class BrokerOrderMapper:
 name="broker_adapter_v1"
 def map(self,r):
  i=r.invocation
  if i.decision is not LiveExecutionDecision.READY:raise ValueError("invocation must be READY")
  if i.mode is not ExecutionMode.LIVE:raise ValueError("invocation must be LIVE")
  q=int(i.quantity);side=BrokerOrderSide.BUY if i.direction is TradeDirection.LONG else BrokerOrderSide.SELL;price=i.entry_price if r.order_type is BrokerOrderType.LIMIT else i.entry_price*0
  cid=client_order_id(i,r,side,q,price,self.name)
  return BrokerOrderRequest(cid,i.invocation_id,i.authorization_id,i.proposal_id,i.request_fingerprint,i.symbol.upper(),side,q,r.order_type,price,r.time_in_force,r.timestamp,i.environment,r.policy.version,self.name,{"deterministic":True})
def client_order_id(i,r,side,q,price,adapter):
 x=json.dumps({"invocation_id":i.invocation_id,"authorization_id":i.authorization_id,"proposal_id":i.proposal_id,"request_fingerprint":i.request_fingerprint,"symbol":i.symbol.upper(),"side":side.value,"quantity":q,"order_type":r.order_type.value,"limit_price":str(price),"time_in_force":r.time_in_force.value,"environment":i.environment,"submitted_at":r.timestamp.isoformat(),"policy_version":r.policy.version,"adapter_version":adapter},sort_keys=True,separators=(",",":"));return hashlib.sha256(x.encode()).hexdigest()

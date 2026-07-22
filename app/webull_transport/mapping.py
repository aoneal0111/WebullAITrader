import hashlib,json
from app.broker_adapter import BrokerOrderSide,BrokerOrderType,BrokerTimeInForce
from app.webull_transport.models import WebullOrderCommand
from app.webull_transport.models_base import *
class WebullOrderMapper:
 name="webull_transport_v1"
 def map(self,r):
  b=r.broker_order_request;action=WebullOrderAction.BUY if b.side is BrokerOrderSide.BUY else WebullOrderAction.SELL;otype=WebullOrderType.LMT if b.order_type is BrokerOrderType.LIMIT else WebullOrderType.MKT;tif=WebullTimeInForce.DAY if b.time_in_force is BrokerTimeInForce.DAY else WebullTimeInForce.GTC;tid=request_id(b,action,otype,tif,self.name)
  return WebullOrderCommand(tid,b.client_order_id,b.symbol,action,b.quantity,otype,b.limit_price,tif,b.environment,b.submitted_at,b.adapter_version,self.name,{"deterministic":True})
def request_id(b,a,o,t,version):
 x=json.dumps({"client_order_id":b.client_order_id,"symbol":b.symbol,"action":a.value,"quantity":b.quantity,"order_type":o.value,"limit_price":str(b.limit_price),"time_in_force":t.value,"environment":b.environment,"submitted_at":b.submitted_at.isoformat(),"adapter_version":b.adapter_version,"transport_version":version},sort_keys=True,separators=(",",":"));return hashlib.sha256(x.encode()).hexdigest()

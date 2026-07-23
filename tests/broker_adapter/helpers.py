from datetime import UTC,datetime
from decimal import Decimal
from app.broker_adapter import *
from app.live_broker import LiveExecutionGuard
from tests.live_broker.helpers import request as live_request
STAMP=datetime(2026,7,21,20,5,tzinfo=UTC)
def invocation(**x):return LiveExecutionGuard().authorize(live_request(**x))
def policy(**x):
 v={"adapter_enabled":True,"maximum_quantity":100,"maximum_notional":Decimal("20000")};v.update(x);return BrokerAdapterPolicy(**v)
def request(**x):
 v={"invocation":invocation(),"timestamp":STAMP,"policy":policy(),"state":BrokerAdapterState(STAMP),"order_type":BrokerOrderType.LIMIT,"time_in_force":BrokerTimeInForce.DAY};v.update(x);return BrokerAdapterRequest(**v)
class FakeTransport:
 def __init__(self,status=BrokerTransportStatus.ACCEPTED,mismatch=False,raise_error=False):self.status=status;self.mismatch=mismatch;self.raise_error=raise_error;self.requests=[]
 def submit_order(self,r):
  if not isinstance(r,BrokerOrderRequest):raise TypeError("BrokerOrderRequest required")
  self.requests.append(r)
  if self.raise_error:raise OSError("controlled")
  return BrokerTransportResponse("wrong" if self.mismatch else r.client_order_id,"transport-1","opaque-1" if self.status is BrokerTransportStatus.ACCEPTED else "",self.status,r.quantity if self.status is BrokerTransportStatus.ACCEPTED else 0,r.limit_price if self.status is BrokerTransportStatus.ACCEPTED else Decimal("0"),r.submitted_at,"","",False)

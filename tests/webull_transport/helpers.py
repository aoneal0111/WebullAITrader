from decimal import Decimal
from app.broker_adapter import BrokerOrderMapper
from app.webull_transport import *
from tests.broker_adapter.helpers import request as adapter_request,STAMP
def order(**x):
 r=adapter_request(**x);return BrokerOrderMapper().map(r)
def policy(**x):
 v={"transport_enabled":True,"maximum_quantity":100,"maximum_notional":Decimal("20000"),"allowed_symbols":("AAPL",)};v.update(x);return WebullTransportPolicy(**v)
class FakeGateway:
 def __init__(self,status=WebullGatewayStatus.ACCEPTED,fail=False,mismatch=False):self.status=status;self.fail=fail;self.mismatch=mismatch;self.commands=[]
 def place_order(self,c):
  if not isinstance(c,WebullOrderCommand):raise TypeError("command required")
  self.commands.append(c)
  if self.fail:raise OSError("controlled")
  return WebullGatewayResponse("wrong" if self.mismatch else c.transport_request_id,c.client_order_id,"opaque" if self.status is WebullGatewayStatus.ACCEPTED else "",self.status,c.quantity if self.status is WebullGatewayStatus.ACCEPTED else 0,c.limit_price if self.status is WebullGatewayStatus.ACCEPTED else Decimal("0"),c.submitted_at,"","",False)
def transport(g=None,p=None,state=None):return WebullTransport(g or FakeGateway(),p or policy(),state or WebullTransportState(STAMP),STAMP)

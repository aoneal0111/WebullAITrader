from datetime import datetime,timezone
from app.open_orders import *
from app.session import Session,SessionIdentifier,SessionSnapshot,SessionStatus
def active_snapshot():
 i=SessionIdentifier("session-1");return SessionSnapshot(SessionStatus.ACTIVE,Session(i,"open orders",SessionStatus.ACTIVE),(),2)
def order(broker_id="broker-1",client_id="client-1",account_id="account-1",symbol="aapl",status=NormalizedOrderStatus.SUBMITTED):return OpenOrderSnapshot(broker_id,client_id,account_id,symbol,OrderSide.BUY,OrderType.LIMIT,status,"10","10","100",submitted_at=datetime(2026,1,1,tzinfo=timezone.utc))
def orders():return (order(),order("broker-2","client-2",symbol="MSFT"))
class FakeSessionManager:
 def __init__(self,snapshot=None,error=None):self.snapshot=snapshot if snapshot is not None else active_snapshot();self.error=error;self.calls=0
 def state(self):
  self.calls+=1
  if self.error:raise self.error
  return self.snapshot
class FakeGateway:
 def __init__(self,response=None,error=None):self.response=orders() if response is None else response;self.error=error;self.requests=[]
 def get_open_orders(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response

from datetime import datetime,timezone
from app.order_status import *
from app.session import Session,SessionIdentifier,SessionSnapshot,SessionStatus
def active_snapshot(session_id="session-1"):
 i=SessionIdentifier(session_id);return SessionSnapshot(SessionStatus.ACTIVE,Session(i,"order status",SessionStatus.ACTIVE),(),2)
def status(state=NormalizedOrderStatus.SUBMITTED,filled="0",remaining="10",price=None,reason=None):return BrokerOrderStatusSnapshot("broker-1","client-1",state,"10",filled,remaining,price,reason,datetime(2026,1,1,tzinfo=timezone.utc))
class FakeSessionManager:
 def __init__(self,snapshot=None,error=None):self.snapshot=snapshot if snapshot is not None else active_snapshot();self.error=error;self.calls=0
 def state(self):
  self.calls+=1
  if self.error:raise self.error
  return self.snapshot
class FakeGateway:
 def __init__(self,response="default",error=None):self.response=status() if response=="default" else response;self.error=error;self.requests=[]
 def get_order_status(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response

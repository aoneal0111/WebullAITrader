from app.order_placement import *
from app.session import Session,SessionIdentifier,SessionSnapshot,SessionStatus
def active_snapshot(session_id="session-1"):
 i=SessionIdentifier(session_id);return SessionSnapshot(SessionStatus.ACTIVE,Session(i,"order placement",SessionStatus.ACTIVE),(),2)
def acknowledgement(accepted=True):return BrokerOrderAcknowledgement("client-1","broker-1" if accepted else "",accepted,NormalizedOrderStatus.SUBMITTED if accepted else NormalizedOrderStatus.REJECTED,"accepted" if accepted else "rejected")
class FakeSessionManager:
 def __init__(self,snapshot=None,error=None):self.snapshot=snapshot if snapshot is not None else active_snapshot();self.error=error;self.calls=0
 def state(self):
  self.calls+=1
  if self.error:raise self.error
  return self.snapshot
class FakeGateway:
 def __init__(self,response=None,error=None):self.response=acknowledgement() if response is None else response;self.error=error;self.requests=[]
 def place_order(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response

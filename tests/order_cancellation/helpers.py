from app.order_cancellation import BrokerOrderCancellationAcknowledgement
from app.session import Session,SessionIdentifier,SessionSnapshot,SessionStatus
def active_snapshot(session_id="session-1"):
 i=SessionIdentifier(session_id);return SessionSnapshot(SessionStatus.ACTIVE,Session(i,"order cancellation",SessionStatus.ACTIVE),(),2)
def acknowledgement(accepted=True,broker_order_id="broker-1",client_order_id="client-1",message=None):return BrokerOrderCancellationAcknowledgement(broker_order_id,client_order_id,accepted,message or ("canceled" if accepted else "rejected"),{"source":"synthetic"})
class FakeSessionManager:
 def __init__(self,snapshot=None,error=None):self.snapshot=snapshot if snapshot is not None else active_snapshot();self.error=error;self.calls=0
 def state(self):
  self.calls+=1
  if self.error:raise self.error
  return self.snapshot
class FakeGateway:
 def __init__(self,response="default",error=None):self.response=acknowledgement() if response=="default" else response;self.error=error;self.requests=[]
 def cancel_order(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response

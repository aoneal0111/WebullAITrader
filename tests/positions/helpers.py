from app.positions import PositionModel
from app.session import Session,SessionIdentifier,SessionSnapshot,SessionStatus
def active_snapshot(session_id="session-1"):
 i=SessionIdentifier(session_id);return SessionSnapshot(SessionStatus.ACTIVE,Session(i,"positions access",SessionStatus.ACTIVE),(),2)
def positions():return (PositionModel("account-1","aapl","EQUITY","2","100.25","220","19.50",None,"usd"),PositionModel("account-1","TSLA","EQUITY","-1","250","-240","10","-2","USD"))
class FakeSessionManager:
 def __init__(self,snapshot=None,error=None):self.snapshot=snapshot if snapshot is not None else active_snapshot();self.error=error;self.calls=0
 def state(self):
  self.calls+=1
  if self.error:raise self.error
  return self.snapshot
class FakeGateway:
 def __init__(self,response=None,error=None):self.response=positions() if response is None else response;self.error=error;self.requests=[]
 def get_positions(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response

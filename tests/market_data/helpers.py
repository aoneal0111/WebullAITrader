from app.market_data import QuoteModel
from app.session import Session,SessionIdentifier,SessionSnapshot,SessionStatus
def active_snapshot(session_id="session-1"):
 i=SessionIdentifier(session_id);return SessionSnapshot(SessionStatus.ACTIVE,Session(i,"market data access",SessionStatus.ACTIVE),(),2)
def quotes():return (QuoteModel("aapl","EQUITY","190.25","190.20","190.30","188","192","187","189",1000,"usd"),QuoteModel("MSFT","EQUITY","420",volume=0))
class FakeSessionManager:
 def __init__(self,snapshot=None,error=None):self.snapshot=snapshot if snapshot is not None else active_snapshot();self.error=error;self.calls=0
 def state(self):
  self.calls+=1
  if self.error:raise self.error
  return self.snapshot
class FakeGateway:
 def __init__(self,response=None,error=None):self.response=quotes() if response is None else response;self.error=error;self.requests=[]
 def get_market_data(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response

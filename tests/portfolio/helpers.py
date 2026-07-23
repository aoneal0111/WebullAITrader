from app.account_information import AccountInformationCriteriaResult,AccountInformationDecision,AccountInformationResult
from app.positions import PositionModel,PositionsCriteriaResult,PositionsDecision,PositionsResult
def account(account_id="account-1",decision=AccountInformationDecision.SUCCESS):
 ok=decision is AccountInformationDecision.SUCCESS;return AccountInformationResult("account-request","session-1",decision,account_id if ok else "","CASH" if ok else "","ACTIVE" if ok else "","1000" if ok else "0","500" if ok else "0","1500" if ok else "0","USD" if ok else "",(AccountInformationCriteriaResult("done",ok,"synthetic"),))
def position(account_id="account-1",symbol="aapl",quantity="2",average_cost="100",market_value="250",unrealized="50"):return PositionModel(account_id,symbol,"EQUITY",quantity,average_cost,market_value,unrealized,None,"USD")
def positions(values=None,decision=PositionsDecision.SUCCESS):return PositionsResult("positions-request","session-1",decision,tuple(values if values is not None else (position(),position(symbol="msft",quantity="1",average_cost="150",market_value="150",unrealized="0"))) if decision is PositionsDecision.SUCCESS else (),(PositionsCriteriaResult("done",decision is PositionsDecision.SUCCESS,"synthetic"),))
class FakeAccountRuntime:
 def __init__(self,response=None,error=None):self.response=response if response is not None else account();self.error=error;self.requests=[]
 def get_account_information(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response
class FakePositionsRuntime:
 def __init__(self,response=None,error=None):self.response=response if response is not None else positions();self.error=error;self.requests=[]
 def get_positions(self,request):
  self.requests.append(request)
  if self.error:raise self.error
  return self.response

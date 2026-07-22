from datetime import timedelta
from app.historical_replay import *
from app.trading_cycle import TradingCycleMode
from tests.execution_orchestrator.helpers import NOW,paper_account,portfolio,real_engine
def provenance(index=0,account=None,**kwargs):
    account=account or paper_account()
    return HistoricalReplayCycleProvenance(kwargs.pop("cycle_id",f"cycle-{index}"),kwargs.pop("mode",TradingCycleMode.BACKTEST),kwargs.pop("started_at",NOW+timedelta(minutes=index)),kwargs.pop("completed_at",NOW+timedelta(minutes=index,seconds=30)),kwargs.pop("portfolio_before",portfolio(False)),account,kwargs)
def event(index=0,event_time=None,sequence=None,event_id=None,request_id=None,symbol="AAPL",quantity="1",**kwargs):
    cycle_provenance=kwargs.pop("cycle_provenance",provenance(index))
    return HistoricalReplayEvent(event_id or f"event-{index}",request_id or f"orchestrator-{index}",index if sequence is None else sequence,symbol,event_time or NOW+timedelta(minutes=index),portfolio(False),cycle_provenance,kwargs.pop("market_price","100"),kwargs.pop("received_time",None),kwargs.pop("bid_price",None),kwargs.pop("ask_price",None),kwargs.pop("available_quantity",None),quantity,{"strategy_configuration":{"order_type":"MARKET"}},kwargs)
def request(events=(),completed=True):return HistoricalReplayRequest(HistoricalReplayIdentity("replay-1","replay-request-1","acct","dataset-1","run-1"),tuple(events),NOW-timedelta(minutes=1),paper_account(),NOW+timedelta(hours=1) if completed else None,{"source":"test"})
class Coordinator:
    def __init__(self,callback=None,errors=None):self.callback=callback;self.errors=errors or {};self.calls=[];self.engine=real_engine()[0]
    def execute(self,req):
        self.calls.append(req)
        if req.request_id in self.errors:raise self.errors[req.request_id]
        return self.callback(req) if self.callback else self.engine.execute(req)
def runtime(coordinator=None,**policy):
    coordinator=coordinator or Coordinator();return HistoricalReplayRuntime(coordinator,HistoricalReplayPolicy(enabled=True,**policy)),coordinator

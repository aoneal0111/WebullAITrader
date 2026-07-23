from datetime import timedelta
from app.trade_journal import TradeJournalPolicy,TradeJournalRuntime,TradeJournalState,TradeJournalStatus
from app.trade_journal_batch import *
from tests.execution_orchestrator.helpers import NOW
from tests.historical_replay.helpers import event
from tests.replay_cycle_projection.helpers import projection,replay
def cycles(count=2):
    source=replay(tuple(event(i) for i in range(count)));runtime,builder,request=projection(source);return runtime.project(request).cycles
def journal():return TradeJournalState("journal-1",TradeJournalStatus.ACTIVE,(),0,None,{"source":"initial"})
def policy():return TradeJournalPolicy(enabled=True)
def item(index,cycle=None,recorded_at=None):
    cycle=cycle or cycles(index+1)[index]
    return TradeJournalBatchItem(f"entry-{index}",cycle,recorded_at or cycle.timing.completed_at+timedelta(seconds=1),{"index":index})
def request(count=2,items=None,initial=None,batch_policy=None,journal_policy=None):
    cs=cycles(count) if items is None else ()
    values=tuple(item(i,c) for i,c in enumerate(cs)) if items is None else tuple(items)
    return TradeJournalBatchRequest(TradeJournalBatchIdentity("batch-1","journal-1","run-1"),initial or journal(),values,journal_policy or policy(),batch_policy or TradeJournalBatchPolicy(),NOW,NOW+timedelta(hours=2),{"test":True})
class Appender:
    def __init__(self,callback=None,errors=None):self.calls=[];self.callback=callback;self.errors=errors or {};self.runtime=TradeJournalRuntime(policy())
    def append(self,req):
        self.calls.append(req)
        if req.entry_id in self.errors:raise self.errors[req.entry_id]
        return self.callback(req) if self.callback else self.runtime.append(req)
class Factory:
    def __init__(self,callback=None,errors=None):self.calls=[];self.callback=callback;self.errors=errors or {};self.default=DeterministicTradeJournalBatchAppendRequestFactory()
    def create(self,batch,cycle,state,index):
        self.calls.append((batch,cycle,state,index))
        if index in self.errors:raise self.errors[index]
        return self.callback(batch,cycle,state,index) if self.callback else self.default.create(batch,cycle,state,index)
def runtime(appender=None,factory=None):
    appender=appender or Appender();factory=factory or Factory();return TradeJournalBatchRuntime(appender,factory),appender,factory

from datetime import timedelta
from app.analytics import AnalyticsPolicy,AnalyticsRuntime,DeterministicAnalyticsEvaluator
from app.backtest_run import *
from app.historical_replay import HistoricalReplayPolicy,HistoricalReplayRuntime
from app.replay_cycle_projection import ReplayCycleProjectionPolicy,ReplayCycleProjectionRuntime
from app.trade_journal import TradeJournalPolicy,TradeJournalRuntime,TradeJournalState,TradeJournalStatus
from app.trade_journal_batch import DeterministicTradeJournalBatchAppendRequestFactory,TradeJournalBatchIdentity,TradeJournalBatchPolicy,TradeJournalBatchRuntime
from app.trading_cycle import TradingCycleBuilder,TradingCyclePolicy
from tests.execution_orchestrator.helpers import NOW,real_engine
from tests.historical_replay.helpers import event,request as replay_request
class Call:
    def __init__(self,method,target=None,error=None,callback=None,log=None,label=None):self.method=method;self.target=target;self.error=error;self.callback=callback;self.calls=[];self.log=log;self.label=label or method
    def __getattr__(self,name):
        if name!=self.method:raise AttributeError(name)
        def invoke(value):
            self.calls.append(value)
            if self.log is not None:self.log.append(self.label)
            if self.error:raise self.error
            if self.callback:return self.callback(value)
            return getattr(self.target,self.method)(value)
        return invoke
class Factory:
    def __init__(self,target,error=None,callback=None,log=None,label="factory"):self.target=target;self.error=error;self.callback=callback;self.calls=[];self.log=log;self.label=label
    def create(self,*args):
        self.calls.append(args)
        if self.log is not None:self.log.append(self.label)
        if self.error:raise self.error
        return self.callback(*args) if self.callback else self.target.create(*args)
def request(count=2,enabled=True,allow_empty=False):
    events=tuple(event(i) for i in range(count));rr=replay_request(events)
    initial=TradeJournalState("journal-1",TradeJournalStatus.ACTIVE,(),0,None,{})
    inputs=tuple(BacktestJournalItemInput(f"cycle-{i}",f"entry-{i}",events[i].cycle_provenance.completed_at+timedelta(seconds=1),{}) for i in range(count))
    journal=BacktestJournalInput(TradeJournalBatchIdentity("batch-1","journal-1","run-1"),initial,inputs,TradeJournalPolicy(enabled=True),TradeJournalBatchPolicy(),NOW,NOW+timedelta(hours=2),{})
    analytics=BacktestAnalyticsInput("analytics-1",NOW+timedelta(hours=2),None,{})
    return BacktestRunRequest(BacktestRunIdentity("run-1","dataset-1"),rr,{},journal,analytics,BacktestRunPolicy(enabled,allow_empty),NOW,NOW+timedelta(hours=3),{})
def components(log=None):
    coordinator=real_engine()[0];replay=HistoricalReplayRuntime(coordinator,HistoricalReplayPolicy(enabled=True,allow_empty_events=True))
    projection=ReplayCycleProjectionRuntime(TradingCycleBuilder(TradingCyclePolicy(enabled=True)),ReplayCycleProjectionPolicy(allow_empty=True))
    journal=TradeJournalBatchRuntime(TradeJournalRuntime(TradeJournalPolicy(enabled=True)),DeterministicTradeJournalBatchAppendRequestFactory())
    analytics=AnalyticsRuntime(DeterministicAnalyticsEvaluator(),AnalyticsPolicy(enabled=True,require_entries=False))
    stages=(Call("replay",replay,log=log,label="replay"),Call("project",projection,log=log,label="projection"),Call("run",journal,log=log,label="journal"),Call("evaluate",analytics,log=log,label="analytics"))
    factories=(Factory(DeterministicProjectionRequestFactory(),log=log,label="projection_factory"),Factory(DeterministicJournalBatchRequestFactory(),log=log,label="journal_factory"),Factory(DeterministicAnalyticsRequestFactory(),log=log,label="analytics_factory"))
    return stages,factories
def runtime(stages=None,factories=None,log=None):
    stages,factories=(stages,factories) if stages and factories else components(log)
    return BacktestRunRuntime(*stages,*factories),stages,factories

from dataclasses import replace
from app.historical_replay import HistoricalReplayEventStatus,HistoricalReplayFailureMode
from app.replay_cycle_projection import *
from app.trading_cycle import TradingCycleBuilder,TradingCyclePolicy
from tests.historical_replay.helpers import Coordinator,event,request,runtime as replay_runtime
class BuilderSpy:
    def __init__(self,callback=None,errors=None):self.calls=[];self.callback=callback;self.errors=errors or {};self.builder=TradingCycleBuilder(TradingCyclePolicy(enabled=True))
    def build(self,req):
        self.calls.append(req)
        if req.cycle_id in self.errors:raise self.errors[req.cycle_id]
        return self.callback(req) if self.callback else self.builder.build(req)
def replay(events=None,coordinator=None,**policy):
    events=events or (event(0),)
    return replay_runtime(coordinator,failure_mode=policy.pop("failure_mode",HistoricalReplayFailureMode.STOP_ON_FAILURE),**policy)[0].replay(request(events))
def projection(replay_result=None,builder=None,**policy):
    builder=builder or BuilderSpy();runtime=ReplayCycleProjectionRuntime(builder,ReplayCycleProjectionPolicy(**policy));return runtime,builder,ReplayCycleProjectionRequest(replay_result or replay())
def failed_replay():
    return replay((event(0),),Coordinator(errors={"orchestrator-0":RuntimeError("boom")}))

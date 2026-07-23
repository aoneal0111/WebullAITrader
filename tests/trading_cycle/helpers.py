from datetime import timedelta
from app.trading_cycle import TradingCycleBuildRequest,TradingCycleMode,TradingCyclePolicy,TradingCycleBuilder
from tests.execution_orchestrator.helpers import NOW,real_engine,request as orchestrator_request

def build_request(signal=None,partial=False,with_position=False,**metadata):
    from app.strategy import StrategySignal
    signal=signal or StrategySignal.BUY;source=orchestrator_request(with_position)
    result=real_engine(signal,partial)[0].execute(source)
    return TradingCycleBuildRequest("cycle-record-1",source.request_id,source.account_id,TradingCycleMode.PAPER,NOW-timedelta(minutes=1),NOW+timedelta(minutes=1),source.portfolio,None,source.paper_account,None,orchestrator_result=result,execution_timestamp=NOW,metadata=metadata)
def builder(evaluator=None,**policy):return TradingCycleBuilder(TradingCyclePolicy(enabled=True,**policy),evaluator)
class Evaluator:
    def __init__(self,response=None,error=None):self.response=response;self.error=error;self.calls=[]
    def evaluate(self,request):
        self.calls.append(request)
        if self.error:raise self.error
        return self.response

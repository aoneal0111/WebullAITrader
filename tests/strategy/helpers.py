from app.strategy import StrategyDecision,StrategySignal
class FakeEvaluator:
 def __init__(self,response=None,error=None):self.response=response if response is not None else (StrategyDecision("AAPL",StrategySignal.HOLD,"0.75",("synthetic hold",)),);self.error=error;self.contexts=[]
 def evaluate(self,context):
  self.contexts.append(context)
  if self.error:raise self.error
  return self.response

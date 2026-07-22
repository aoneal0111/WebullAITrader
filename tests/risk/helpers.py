from app.risk import DeterministicRiskEvaluator
class FakeEvaluator:
 def __init__(self,response=None,error=None):self.response=response;self.error=error;self.calls=[];self.delegate=DeterministicRiskEvaluator()
 def evaluate(self,context,policy):
  self.calls.append((context,policy))
  if self.error:raise self.error
  return self.response if self.response is not None else self.delegate.evaluate(context,policy)

from app.execution_planner import DeterministicExecutionPlannerEvaluator
class FakeEvaluator:
 def __init__(self,response=None,error=None):self.response=response;self.error=error;self.calls=[];self.delegate=DeterministicExecutionPlannerEvaluator()
 def evaluate(self,request,policy):
  self.calls.append((request,policy))
  if self.error:raise self.error
  return self.response if self.response is not None else self.delegate.evaluate(request,policy)

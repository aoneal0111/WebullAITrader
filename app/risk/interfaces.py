from typing import Protocol
from app.risk.models import RiskContext,RiskResult
from app.risk.policies import RiskPolicy
class RiskEvaluator(Protocol):
 def evaluate(self,context:RiskContext,policy:RiskPolicy)->RiskResult:...
class RiskRuntime(Protocol):
 def evaluate(self,context:RiskContext)->RiskResult:...

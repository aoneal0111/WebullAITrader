from typing import Protocol
from app.parameter_sweep import ParameterSweepRequest,ParameterSweepResult
class ParameterSweepExecutor(Protocol):
    def run(self,request:ParameterSweepRequest)->ParameterSweepResult:...

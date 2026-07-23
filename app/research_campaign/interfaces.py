from typing import Protocol
from app.experiment import ExperimentRequest,ExperimentResult
class ExperimentExecutor(Protocol):
    def run(self,request:ExperimentRequest)->ExperimentResult:...

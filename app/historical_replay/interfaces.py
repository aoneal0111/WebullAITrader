from typing import Protocol
from app.execution_orchestrator import PaperTradingCycleRequest,PaperTradingCycleResult
class HistoricalReplayCoordinator(Protocol):
    def execute(self,request:PaperTradingCycleRequest)->PaperTradingCycleResult:...

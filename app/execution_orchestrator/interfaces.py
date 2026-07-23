from typing import Protocol

from app.execution_orchestrator.models import PaperTradingCycleRequest, PaperTradingCycleResult


class ExecutionOrchestrator(Protocol):
    def execute(self, request: PaperTradingCycleRequest) -> PaperTradingCycleResult: ...

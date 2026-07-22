from typing import Protocol
from app.trading_cycle.models import TradingCycleBuildRequest,TradingCycleMetrics
class TradingCycleMetricsEvaluator(Protocol):
    def evaluate(self,request:TradingCycleBuildRequest)->TradingCycleMetrics:...

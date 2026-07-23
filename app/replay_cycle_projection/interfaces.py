from typing import Protocol
from app.trading_cycle import TradingCycleBuildRequest,TradingCycleBuildResult
class ReplayCycleBuilder(Protocol):
    def build(self,request:TradingCycleBuildRequest)->TradingCycleBuildResult:...

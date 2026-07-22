from typing import Protocol
from app.backtest_suite import BacktestSuiteRequest,BacktestSuiteResult
class BacktestSuiteExecutor(Protocol):
    def run(self,request:BacktestSuiteRequest)->BacktestSuiteResult:...

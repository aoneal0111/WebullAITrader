from typing import Protocol
from app.backtest_run import BacktestRunRequest,BacktestRunResult
from app.backtest_report import BacktestReportRequest,BacktestReportResult
class BacktestRunExecutor(Protocol):
    def run(self,request:BacktestRunRequest)->BacktestRunResult:...
class BacktestReportExecutor(Protocol):
    def create(self,request:BacktestReportRequest)->BacktestReportResult:...

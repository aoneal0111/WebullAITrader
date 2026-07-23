from app.backtest_suite import BacktestSuiteStatus
from tests.backtest_suite.helpers import request,runtime
def test_runtime_has_no_cross_call_state():
    engine,runs,reports=runtime();a=engine.run(request(2));b=engine.run(request(0));c=engine.run(request(1,enabled=False))
    assert a.status is BacktestSuiteStatus.COMPLETED and b.status is BacktestSuiteStatus.EMPTY and c.status is BacktestSuiteStatus.DISABLED
    assert len(a.items)==2 and b.items==c.items==()

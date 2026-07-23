from app.parameter_sweep import ParameterSweepStatus
from tests.parameter_sweep.helpers import request,runtime
def test_same_runtime_has_no_cross_call_state():
    engine,executor=runtime();a=engine.run(request(2));b=engine.run(request(0));c=engine.run(request(1,enabled=False))
    assert a.status is ParameterSweepStatus.COMPLETED and b.status is ParameterSweepStatus.EMPTY and c.status is ParameterSweepStatus.DISABLED
    assert len(a.cases)==2 and b.cases==c.cases==()

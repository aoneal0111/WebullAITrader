import pytest
from app.broker_execution import *
from tests.broker_execution.helpers import request
class FakePort:
    def execute(self,a):
        if not isinstance(a,BrokerExecutionAuthorization):raise TypeError("authorization required")
        if a.decision is not SafetyDecision.APPROVED:raise ValueError("approval required")
        return a.authorization_id
def test_port_accepts_only_approved_authorization():
    p=FakePort();r=request();a=ExecutionSafetyGate().authorize(r);assert p.execute(a)==a.authorization_id
    with pytest.raises(TypeError):p.execute(r.proposal)

import pytest
from app.live_broker import *
from tests.live_broker.helpers import request
class FakePort:
 def execute(self,x):
  if not isinstance(x,LiveBrokerInvocation):raise TypeError("invocation required")
  if x.decision is not LiveExecutionDecision.READY:raise ValueError("ready invocation required")
  return x.invocation_id
def test_only_ready_invocation_accepted():
 r=request();i=LiveExecutionGuard().authorize(r);assert FakePort().execute(i)==i.invocation_id
 for x in (r,r.authorization):
  with pytest.raises(TypeError):FakePort().execute(x)

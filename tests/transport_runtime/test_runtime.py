import pytest
from app.transport_runtime import *
from tests.transport_runtime.helpers import *
def test_exactly_once_and_deterministic_record():
 e=FakeExecutor();r=request();a=TransportRuntime(e,policy()).execute(r);b=TransportRuntime(FakeExecutor(),policy()).execute(r);assert a==b and e.requests==[r]
def test_disabled_retries_and_failure_blockers():
 with pytest.raises(TransportValidationError):TransportRuntime(FakeExecutor(),TransportRuntimePolicy()).execute(request())
 with pytest.raises(TransportValidationError):TransportRuntime(FakeExecutor(),policy(retries_enabled=True))
 with pytest.raises(TransportExecutionError):TransportRuntime(FakeExecutor(fail=True),policy()).execute(request())
def test_hooks_are_injected_and_called_once():
 class Rate:
  def __init__(self):self.items=[]
  def allow(self,r):self.items.append(r);return True
 class Telemetry:
  def __init__(self):self.items=[]
  def record(self,r):self.items.append(r)
 rate=Rate();telemetry=Telemetry();record=TransportRuntime(FakeExecutor(),policy(rate_limit_enabled=True,telemetry_enabled=True),telemetry,rate).execute(request());assert rate.items==[record.request] and telemetry.items==[record]

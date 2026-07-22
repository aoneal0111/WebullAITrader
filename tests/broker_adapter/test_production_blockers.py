import pytest
from app.broker_adapter import BrokerAdapter,BrokerAdapterPolicy
from tests.broker_adapter.helpers import FakeTransport,request
def test_default_policy_and_raw_inputs_never_reach_transport():
 t=FakeTransport();assert BrokerAdapter(t).execute(request(policy=BrokerAdapterPolicy())).metadata["transport_invoked"] is False
 for raw in (request().invocation,request().invocation.authorization_id):
  with pytest.raises(ValueError):BrokerAdapter(t).execute(raw)
 assert not t.requests

import pytest
from tests.broker_adapter.helpers import FakeTransport,request
def test_transport_only_accepts_mapped_order():
 t=FakeTransport();r=request()
 for raw in (r,r.invocation):
  with pytest.raises(TypeError):t.submit_order(raw)

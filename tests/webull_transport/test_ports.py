import pytest
from tests.webull_transport.helpers import FakeGateway,order
def test_gateway_only_accepts_command():
 with pytest.raises(TypeError):FakeGateway().place_order(order())

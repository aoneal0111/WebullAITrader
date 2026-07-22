import pytest
from app.webull_transport import *
from tests.webull_transport.helpers import FakeGateway,order
from tests.broker_adapter.helpers import STAMP
def test_default_disabled_and_raw_types_blocked():
 g=FakeGateway();x=WebullTransport(g,WebullTransportPolicy(),WebullTransportState(STAMP),STAMP).submit_order(order());assert x.status.value=="FAILED" and not g.commands
 with pytest.raises(ValueError):WebullTransport(g,WebullTransportPolicy(),WebullTransportState(STAMP),STAMP).submit_order(object())
 assert not hasattr(WebullTransport,"login") and not hasattr(WebullTransport,"refresh_session")

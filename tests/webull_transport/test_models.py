from dataclasses import FrozenInstanceError
import json,pytest
from app.webull_transport import *
from tests.webull_transport.helpers import order,policy
from tests.broker_adapter.helpers import STAMP
def test_roundtrips():
 r=WebullTransportRequest(order(),STAMP,policy(),WebullTransportState(STAMP));assert WebullTransportRequest.from_dict(r.to_dict())==r
 c=WebullOrderMapper().map(r);assert WebullOrderCommand.from_dict(c.to_dict())==c;json.dumps(c.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):c.quantity=1

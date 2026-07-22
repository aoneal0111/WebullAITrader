from dataclasses import FrozenInstanceError
import json,pytest
from app.paper_broker import *
from tests.paper_broker.helpers import request
def test_roundtrips_and_frozen():
 r=request(metadata={"x":[1]});assert PaperBrokerExecutionRequest.from_dict(r.to_dict())==r
 result=PaperBrokerAdapter().execute(r);assert PaperBrokerExecutionResult.from_dict(result.to_dict())==result;json.dumps(result.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):result.status=None

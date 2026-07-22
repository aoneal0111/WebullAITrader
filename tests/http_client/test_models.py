from dataclasses import FrozenInstanceError
import json,pytest
from app.http_client import *
from tests.http_runtime.helpers import request
def test_serialized_model_roundtrip_frozen():
 x=HTTPRequestSerializer().serialize(request());assert SerializedHTTPRequest.from_dict(x.to_dict())==x;json.dumps(x.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):x.url="x"

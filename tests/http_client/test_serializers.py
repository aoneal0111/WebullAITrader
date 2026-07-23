import pytest
from app.http_client import *
from tests.http_runtime.helpers import request
def test_serializer_preserves_protocol_fields():
 r=request();x=HTTPRequestSerializer().serialize(r);assert x.request_id==r.request_id and x.correlation_id==r.correlation_id and x.body==r.body
def test_serializer_rejects_raw():
 with pytest.raises(HTTPSerializationError):HTTPRequestSerializer().serialize({})

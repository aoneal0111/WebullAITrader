from datetime import timedelta
import pytest
from app.http_client import *
from tests.http_runtime.helpers import request
def test_parser_preserves_correlation():
 r=request();raw=SerializedHTTPResponse(200,{}, {"ok":True},r.correlation_id,r.timestamp+timedelta(seconds=1));assert HTTPResponseParser().parse(raw,r.correlation_id).correlation_id==r.correlation_id
def test_parser_rejects_mismatch():
 r=request();raw=SerializedHTTPResponse(200,{}, {},"wrong",r.timestamp)
 with pytest.raises(HTTPParsingError):HTTPResponseParser().parse(raw,r.correlation_id)

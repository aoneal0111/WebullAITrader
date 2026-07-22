import pytest
from app.http_client import *
from tests.http_client.helpers import *
from tests.http_runtime.helpers import request
def test_execute_once_deterministic():
 t=FakeTransport();r=request();a=HTTPClient(t,policy()).execute(r);b=HTTPClient(FakeTransport(),policy()).execute(r);assert a==b and len(t.requests)==1
def test_disabled_optional_and_transport_failure():
 with pytest.raises(HTTPClientValidationError):HTTPClient(FakeTransport(),HTTPClientPolicy()).execute(request())
 for x in ({"retries_enabled":True},{"redirects_enabled":True},{"cookies_enabled":True},{"compression_enabled":True}):
  with pytest.raises(HTTPClientValidationError):HTTPClient(FakeTransport(),policy(**x))
 with pytest.raises(HTTPTransportError):HTTPClient(FakeTransport(fail=True),policy()).execute(request())

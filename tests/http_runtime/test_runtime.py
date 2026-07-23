import pytest
from app.http_runtime import *
from tests.http_runtime.helpers import *
def test_exactly_once_deterministic():
 e=FakeHTTPExecutor();r=request();a=HTTPRuntime(e,policy()).execute(r);b=HTTPRuntime(FakeHTTPExecutor(),policy()).execute(r);assert a==b and e.requests==[r]
def test_disabled_optional_features_and_failures():
 with pytest.raises(HTTPValidationError):HTTPRuntime(FakeHTTPExecutor(),HTTPRuntimePolicy()).execute(request())
 for x in ({"redirects_enabled":True},{"cookies_enabled":True},{"compression_enabled":True}):
  with pytest.raises(HTTPValidationError):HTTPRuntime(FakeHTTPExecutor(),policy(**x))
 with pytest.raises(HTTPExecutionError):HTTPRuntime(FakeHTTPExecutor(fail=True),policy()).execute(request())

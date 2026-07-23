from app.session_bootstrap import *
from tests.session_bootstrap.fixtures import request
from tests.session_bootstrap.test_runtime import build
def test_deterministic_safe_serialization():
 r=request();result=build()[0].bootstrap(r);values=(serialize_policy(SessionBootstrapPolicy()),serialize_request(r),serialize_criteria(result.criteria_results[0]),serialize_result(result));assert values==tuple(dict(x) for x in values);rendered=repr(values);assert "opaque-secret" not in rendered;assert "token" not in rendered

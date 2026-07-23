import inspect
from app.http_runtime import HTTPRuntime,HTTPRuntimePolicy
def test_no_production_http_behavior():
 assert not HTTPRuntimePolicy().runtime_enabled;source=inspect.getsource(HTTPRuntime)
 for x in ("sleep(","Thread(","create_task","datetime.now","uuid","random"):
  assert x not in source

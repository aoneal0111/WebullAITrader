import inspect
from app.transport_runtime import TransportRuntime,TransportRuntimePolicy
def test_default_disabled_and_no_execution_capabilities():
 assert not TransportRuntimePolicy().runtime_enabled
 source=inspect.getsource(TransportRuntime)
 for prohibited in ("sleep(","create_task","Thread(","datetime.now","uuid","random"):
  assert prohibited not in source

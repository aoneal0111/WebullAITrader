from tests.transport_runtime.helpers import FakeExecutor
def test_fake_executor_exposes_exact_execution_method():
 assert hasattr(FakeExecutor(),"execute")

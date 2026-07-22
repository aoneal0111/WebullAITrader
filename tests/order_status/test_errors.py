from app.order_status import *
def test_hierarchy():
 for error in (OrderStatusValidationError,OrderStatusDependencyError,OrderStatusGatewayError,OrderStatusIdentityError,OrderStatusSnapshotError,OrderStatusSerializationError):assert issubclass(error,OrderStatusError)

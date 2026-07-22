from app.open_orders import *
def test_hierarchy():
 for error in (OpenOrdersValidationError,OpenOrdersDependencyError,OpenOrdersGatewayError,OpenOrdersSnapshotError,OpenOrdersIdentityError,OpenOrdersSerializationError):assert issubclass(error,OpenOrdersError)

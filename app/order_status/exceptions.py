class OrderStatusError(Exception):
    """Base error for deterministic order-status retrieval."""
class OrderStatusValidationError(OrderStatusError): pass
class OrderStatusDependencyError(OrderStatusError): pass
class OrderStatusGatewayError(OrderStatusError): pass
class OrderStatusIdentityError(OrderStatusError): pass
class OrderStatusSnapshotError(OrderStatusError): pass
class OrderStatusSerializationError(OrderStatusError): pass

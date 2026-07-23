class OrderCancellationError(Exception):
    """Base error for deterministic order cancellation."""


class OrderCancellationValidationError(OrderCancellationError): pass
class OrderCancellationDependencyError(OrderCancellationError): pass
class OrderCancellationGatewayError(OrderCancellationError): pass
class OrderCancellationIdentityError(OrderCancellationError): pass
class OrderCancellationAcknowledgementError(OrderCancellationError): pass
class OrderCancellationSerializationError(OrderCancellationError): pass

class OrderPlacementError(Exception):
    """Base error for deterministic order placement."""
class OrderPlacementValidationError(OrderPlacementError): pass
class OrderPlacementDependencyError(OrderPlacementError): pass
class OrderPlacementSerializationError(OrderPlacementError): pass

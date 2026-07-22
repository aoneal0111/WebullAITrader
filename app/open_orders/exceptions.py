class OpenOrdersError(Exception):
    """Base error for deterministic open-order retrieval."""
class OpenOrdersValidationError(OpenOrdersError): pass
class OpenOrdersDependencyError(OpenOrdersError): pass
class OpenOrdersGatewayError(OpenOrdersError): pass
class OpenOrdersSnapshotError(OpenOrdersError): pass
class OpenOrdersIdentityError(OpenOrdersError): pass
class OpenOrdersSerializationError(OpenOrdersError): pass

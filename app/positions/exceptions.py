class PositionsError(Exception):
    """Base error for deterministic position retrieval."""


class PositionsValidationError(PositionsError): pass
class PositionsDependencyError(PositionsError): pass
class PositionsSerializationError(PositionsError): pass

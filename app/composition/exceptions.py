class CompositionError(ValueError):
    """Base error for deterministic composition failures."""


class DuplicateRegistrationError(CompositionError):
    pass


class MissingDependencyError(CompositionError):
    pass


class CircularDependencyError(CompositionError):
    pass


class FactoryValidationError(CompositionError):
    pass

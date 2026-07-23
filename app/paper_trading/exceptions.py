class PaperTradingError(Exception):
    """Base error for deterministic paper trading."""


class PaperTradingValidationError(PaperTradingError):
    pass


class PaperTradingDependencyError(PaperTradingError):
    pass


class PaperTradingEvaluationError(PaperTradingError):
    pass


class PaperTradingSerializationError(PaperTradingError):
    pass

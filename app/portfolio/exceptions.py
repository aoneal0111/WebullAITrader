class PortfolioError(Exception):
    """Base error for deterministic portfolio composition."""
class PortfolioValidationError(PortfolioError): pass
class PortfolioDependencyError(PortfolioError): pass
class PortfolioCompositionError(PortfolioError): pass
class PortfolioSerializationError(PortfolioError): pass

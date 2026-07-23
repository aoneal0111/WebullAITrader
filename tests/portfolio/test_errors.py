from app.portfolio import *
def test_hierarchy():
 for error in (PortfolioValidationError,PortfolioDependencyError,PortfolioCompositionError,PortfolioSerializationError):assert issubclass(error,PortfolioError)

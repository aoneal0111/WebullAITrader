from app.paper_trading.exceptions import PaperTradingSerializationError
from app.paper_trading.milestone_models import (PaperExecutionRequest, PaperExecutionResult, PaperFill, PaperOrder,
                                      PaperPortfolioSnapshot, PaperPosition, PaperTradingAccount,
                                      PaperTradingCriteriaResult)
from app.paper_trading.policies import PaperTradingPolicy


def _serialize(value, expected):
    if not isinstance(value, expected): raise PaperTradingSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()


serialize_account = lambda value: _serialize(value, PaperTradingAccount)
serialize_position = lambda value: _serialize(value, PaperPosition)
serialize_order = lambda value: _serialize(value, PaperOrder)
serialize_fill = lambda value: _serialize(value, PaperFill)
serialize_portfolio = lambda value: _serialize(value, PaperPortfolioSnapshot)
serialize_request = lambda value: _serialize(value, PaperExecutionRequest)
serialize_result = lambda value: _serialize(value, PaperExecutionResult)
serialize_criteria = lambda value: _serialize(value, PaperTradingCriteriaResult)
serialize_policy = lambda value: _serialize(value, PaperTradingPolicy)

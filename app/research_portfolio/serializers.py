from app.research_portfolio.exceptions import ResearchPortfolioSerializationError
from app.research_portfolio.models import *
def _serialize(value,expected):
    if not isinstance(value,expected):raise ResearchPortfolioSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
serialize_policy=lambda v:_serialize(v,ResearchPortfolioPolicy)
serialize_identity=lambda v:_serialize(v,ResearchPortfolioIdentity)
serialize_program_identity=lambda v:_serialize(v,ResearchPortfolioProgramIdentity)
serialize_program_request=lambda v:_serialize(v,ResearchPortfolioProgramRequest)
serialize_request=lambda v:_serialize(v,ResearchPortfolioRequest)
serialize_criteria=lambda v:_serialize(v,ResearchPortfolioCriteriaResult)
serialize_program_record=lambda v:_serialize(v,ResearchPortfolioProgramRecord)
serialize_summary=lambda v:_serialize(v,ResearchPortfolioSummary)
serialize_result=lambda v:_serialize(v,ResearchPortfolioResult)

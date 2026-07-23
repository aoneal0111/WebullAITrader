from app.research_portfolio.exceptions import ResearchPortfolioDependencyError,ResearchPortfolioValidationError
from app.research_portfolio.models import ResearchPortfolioRequest
def validate_dependencies(executor):
    if executor is None or isinstance(executor,type) or not callable(getattr(executor,"run",None)):raise ResearchPortfolioDependencyError("research program executor must be an instance exposing run(request)")
def validate_request(request,minimal=False):
    if not isinstance(request,ResearchPortfolioRequest):raise ResearchPortfolioValidationError("request must be ResearchPortfolioRequest")
    if minimal:return request
    errors=[];seen_entries=set();seen_programs=set()
    for program in request.programs:
        if program.identity.program_id!=program.program_request.identity.program_id:errors.append(f"program identity mismatch at program entry {program.identity.program_entry_id}")
        for value,seen,label in ((program.identity.program_entry_id,seen_entries,"program entry ID"),(program.identity.program_id,seen_programs,"program ID")):
            if value in seen:errors.append(f"duplicate {label} at program entry {program.identity.program_entry_id}")
            seen.add(value)
    return tuple(errors)

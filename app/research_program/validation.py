from app.research_program.exceptions import ResearchProgramDependencyError,ResearchProgramValidationError
from app.research_program.models import ResearchProgramRequest
def validate_dependencies(executor):
    if executor is None or isinstance(executor,type) or not callable(getattr(executor,"run",None)):raise ResearchProgramDependencyError("research study executor must be an instance exposing run(request)")
def validate_request(request,minimal=False):
    if not isinstance(request,ResearchProgramRequest):raise ResearchProgramValidationError("request must be ResearchProgramRequest")
    if minimal:return request
    errors=[];seen_entries=set();seen_studies=set()
    for study in request.studies:
        if study.identity.study_id!=study.study_request.identity.study_id:errors.append(f"study identity mismatch at study entry {study.identity.study_entry_id}")
        for value,seen,label in ((study.identity.study_entry_id,seen_entries,"study entry ID"),(study.identity.study_id,seen_studies,"study ID")):
            if value in seen:errors.append(f"duplicate {label} at study entry {study.identity.study_entry_id}")
            seen.add(value)
    return tuple(errors)

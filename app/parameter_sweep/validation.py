from app.parameter_sweep.exceptions import ParameterSweepDependencyError,ParameterSweepValidationError
from app.parameter_sweep.models import ParameterSweepRequest
def validate_dependencies(executor):
    if executor is None or not callable(getattr(executor,"run",None)):raise ParameterSweepDependencyError("suite executor must expose run(request)")
def validate_request(request,minimal=False):
    if not isinstance(request,ParameterSweepRequest):raise ParameterSweepValidationError("request must be ParameterSweepRequest")
    if minimal:return request
    errors=[];seen_cases=set();seen_suites=set()
    for case in request.cases:
        if case.identity.suite_id!=case.suite_request.identity.suite_id:errors.append(f"suite identity mismatch at case {case.identity.case_id}")
        for value,seen,label in ((case.identity.case_id,seen_cases,"case ID"),(case.identity.suite_id,seen_suites,"suite ID")):
            if value in seen:errors.append(f"duplicate {label} at case {case.identity.case_id}")
            seen.add(value)
    return tuple(errors)

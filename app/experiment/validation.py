from app.experiment.exceptions import ExperimentDependencyError,ExperimentValidationError
from app.experiment.models import ExperimentRequest
def validate_dependencies(executor):
    if executor is None or isinstance(executor,type) or not callable(getattr(executor,"run",None)):raise ExperimentDependencyError("parameter sweep executor must be an instance exposing run(request)")
def validate_request(request,minimal=False):
    if not isinstance(request,ExperimentRequest):raise ExperimentValidationError("request must be ExperimentRequest")
    if minimal:return request
    errors=[];seen_entries=set();seen_sweeps=set()
    for sweep in request.sweeps:
        if sweep.identity.parameter_sweep_id!=sweep.parameter_sweep_request.identity.sweep_id:errors.append(f"parameter sweep identity mismatch at sweep entry {sweep.identity.sweep_entry_id}")
        for value,seen,label in ((sweep.identity.sweep_entry_id,seen_entries,"sweep entry ID"),(sweep.identity.parameter_sweep_id,seen_sweeps,"parameter sweep ID")):
            if value in seen:errors.append(f"duplicate {label} at sweep entry {sweep.identity.sweep_entry_id}")
            seen.add(value)
    return tuple(errors)

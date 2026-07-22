from app.parameter_sweep.exceptions import ParameterSweepSerializationError
from app.parameter_sweep.models import *
def _s(v,t):
    if not isinstance(v,t):raise ParameterSweepSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_policy=lambda v:_s(v,ParameterSweepPolicy)
serialize_identity=lambda v:_s(v,ParameterSweepIdentity)
serialize_case_identity=lambda v:_s(v,ParameterSweepCaseIdentity)
serialize_case_request=lambda v:_s(v,ParameterSweepCaseRequest)
serialize_request=lambda v:_s(v,ParameterSweepRequest)
serialize_criteria=lambda v:_s(v,ParameterSweepCriteriaResult)
serialize_case_record=lambda v:_s(v,ParameterSweepCaseRecord)
serialize_summary=lambda v:_s(v,ParameterSweepSummary)
serialize_result=lambda v:_s(v,ParameterSweepResult)

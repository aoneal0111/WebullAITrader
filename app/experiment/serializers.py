from app.experiment.exceptions import ExperimentSerializationError
from app.experiment.models import *
def _serialize(value,expected):
    if not isinstance(value,expected):raise ExperimentSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
serialize_policy=lambda v:_serialize(v,ExperimentPolicy)
serialize_identity=lambda v:_serialize(v,ExperimentIdentity)
serialize_sweep_identity=lambda v:_serialize(v,ExperimentSweepIdentity)
serialize_sweep_request=lambda v:_serialize(v,ExperimentSweepRequest)
serialize_request=lambda v:_serialize(v,ExperimentRequest)
serialize_criteria=lambda v:_serialize(v,ExperimentCriteriaResult)
serialize_sweep_record=lambda v:_serialize(v,ExperimentSweepRecord)
serialize_summary=lambda v:_serialize(v,ExperimentSummary)
serialize_result=lambda v:_serialize(v,ExperimentResult)

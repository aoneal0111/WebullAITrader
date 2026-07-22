from app.research_program.exceptions import ResearchProgramSerializationError
from app.research_program.models import *
def _serialize(value,expected):
    if not isinstance(value,expected):raise ResearchProgramSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
serialize_policy=lambda v:_serialize(v,ResearchProgramPolicy)
serialize_identity=lambda v:_serialize(v,ResearchProgramIdentity)
serialize_study_identity=lambda v:_serialize(v,ResearchProgramStudyIdentity)
serialize_study_request=lambda v:_serialize(v,ResearchProgramStudyRequest)
serialize_request=lambda v:_serialize(v,ResearchProgramRequest)
serialize_criteria=lambda v:_serialize(v,ResearchProgramCriteriaResult)
serialize_study_record=lambda v:_serialize(v,ResearchProgramStudyRecord)
serialize_summary=lambda v:_serialize(v,ResearchProgramSummary)
serialize_result=lambda v:_serialize(v,ResearchProgramResult)

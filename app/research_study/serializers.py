from app.research_study.exceptions import ResearchStudySerializationError
from app.research_study.models import *
def _serialize(value,expected):
    if not isinstance(value,expected):raise ResearchStudySerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
serialize_policy=lambda v:_serialize(v,ResearchStudyPolicy)
serialize_identity=lambda v:_serialize(v,ResearchStudyIdentity)
serialize_campaign_identity=lambda v:_serialize(v,ResearchStudyCampaignIdentity)
serialize_campaign_request=lambda v:_serialize(v,ResearchStudyCampaignRequest)
serialize_request=lambda v:_serialize(v,ResearchStudyRequest)
serialize_criteria=lambda v:_serialize(v,ResearchStudyCriteriaResult)
serialize_campaign_record=lambda v:_serialize(v,ResearchStudyCampaignRecord)
serialize_summary=lambda v:_serialize(v,ResearchStudySummary)
serialize_result=lambda v:_serialize(v,ResearchStudyResult)

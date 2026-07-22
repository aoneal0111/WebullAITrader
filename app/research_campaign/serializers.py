from app.research_campaign.exceptions import ResearchCampaignSerializationError
from app.research_campaign.models import *
def _serialize(value,expected):
    if not isinstance(value,expected):raise ResearchCampaignSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
serialize_policy=lambda v:_serialize(v,ResearchCampaignPolicy)
serialize_identity=lambda v:_serialize(v,ResearchCampaignIdentity)
serialize_experiment_identity=lambda v:_serialize(v,ResearchCampaignExperimentIdentity)
serialize_experiment_request=lambda v:_serialize(v,ResearchCampaignExperimentRequest)
serialize_request=lambda v:_serialize(v,ResearchCampaignRequest)
serialize_criteria=lambda v:_serialize(v,ResearchCampaignCriteriaResult)
serialize_experiment_record=lambda v:_serialize(v,ResearchCampaignExperimentRecord)
serialize_summary=lambda v:_serialize(v,ResearchCampaignSummary)
serialize_result=lambda v:_serialize(v,ResearchCampaignResult)

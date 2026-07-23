from app.research_study.exceptions import ResearchStudyDependencyError,ResearchStudyValidationError
from app.research_study.models import ResearchStudyRequest
def validate_dependencies(executor):
    if executor is None or isinstance(executor,type) or not callable(getattr(executor,"run",None)):raise ResearchStudyDependencyError("research campaign executor must be an instance exposing run(request)")
def validate_request(request,minimal=False):
    if not isinstance(request,ResearchStudyRequest):raise ResearchStudyValidationError("request must be ResearchStudyRequest")
    if minimal:return request
    errors=[];seen_entries=set();seen_campaigns=set()
    for campaign in request.campaigns:
        if campaign.identity.campaign_id!=campaign.campaign_request.identity.campaign_id:errors.append(f"campaign identity mismatch at campaign entry {campaign.identity.campaign_entry_id}")
        for value,seen,label in ((campaign.identity.campaign_entry_id,seen_entries,"campaign entry ID"),(campaign.identity.campaign_id,seen_campaigns,"campaign ID")):
            if value in seen:errors.append(f"duplicate {label} at campaign entry {campaign.identity.campaign_entry_id}")
            seen.add(value)
    return tuple(errors)

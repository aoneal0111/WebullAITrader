from app.research_campaign.exceptions import ResearchCampaignDependencyError,ResearchCampaignValidationError
from app.research_campaign.models import ResearchCampaignRequest
def validate_dependencies(executor):
    if executor is None or isinstance(executor,type) or not callable(getattr(executor,"run",None)):raise ResearchCampaignDependencyError("experiment executor must be an instance exposing run(request)")
def validate_request(request,minimal=False):
    if not isinstance(request,ResearchCampaignRequest):raise ResearchCampaignValidationError("request must be ResearchCampaignRequest")
    if minimal:return request
    errors=[];seen_entries=set();seen_experiments=set()
    for experiment in request.experiments:
        if experiment.identity.experiment_id!=experiment.experiment_request.identity.experiment_id:errors.append(f"experiment identity mismatch at experiment entry {experiment.identity.experiment_entry_id}")
        for value,seen,label in ((experiment.identity.experiment_entry_id,seen_entries,"experiment entry ID"),(experiment.identity.experiment_id,seen_experiments,"experiment ID")):
            if value in seen:errors.append(f"duplicate {label} at experiment entry {experiment.identity.experiment_entry_id}")
            seen.add(value)
    return tuple(errors)

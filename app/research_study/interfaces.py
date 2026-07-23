from typing import Protocol
from app.research_campaign import ResearchCampaignRequest,ResearchCampaignResult
class ResearchCampaignExecutor(Protocol):
    def run(self,request:ResearchCampaignRequest)->ResearchCampaignResult:...

from typing import Protocol
from app.analytics.domain_models import AnalyticsRequest,AnalyticsSummary
from app.analytics.policies import AnalyticsPolicy
class AnalyticsEvaluator(Protocol):
    def evaluate(self,request:AnalyticsRequest,policy:AnalyticsPolicy)->AnalyticsSummary:...

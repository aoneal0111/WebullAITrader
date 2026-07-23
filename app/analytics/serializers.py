from app.analytics.domain_models import *
from app.analytics.exceptions import AnalyticsSerializationError
from app.analytics.policies import AnalyticsPolicy
def _s(v,t):
    if not isinstance(v,t):raise AnalyticsSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_request=lambda v:_s(v,AnalyticsRequest)
serialize_result=lambda v:_s(v,AnalyticsResult)
serialize_summary=lambda v:_s(v,AnalyticsSummary)
serialize_metrics=lambda v:_s(v,AnalyticsMetrics)
serialize_policy=lambda v:_s(v,AnalyticsPolicy)
serialize_criteria=lambda v:_s(v,AnalyticsCriteriaResult)
serialize_equity_point=lambda v:_s(v,EquityPoint)
serialize_drawdown_point=lambda v:_s(v,DrawdownPoint)

from app.backtest_suite.exceptions import BacktestSuiteSerializationError
from app.backtest_suite.models import *
def _s(v,t):
    if not isinstance(v,t):raise BacktestSuiteSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_policy=lambda v:_s(v,BacktestSuitePolicy)
serialize_identity=lambda v:_s(v,BacktestSuiteIdentity)
serialize_item_identity=lambda v:_s(v,BacktestSuiteItemIdentity)
serialize_item_request=lambda v:_s(v,BacktestSuiteItemRequest)
serialize_request=lambda v:_s(v,BacktestSuiteRequest)
serialize_criteria=lambda v:_s(v,BacktestSuiteCriteriaResult)
serialize_item_record=lambda v:_s(v,BacktestSuiteItemRecord)
serialize_summary=lambda v:_s(v,BacktestSuiteSummary)
serialize_result=lambda v:_s(v,BacktestSuiteResult)

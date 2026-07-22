from app.trading_cycle.exceptions import TradingCycleSerializationError
from app.trading_cycle.models import *
def _serialize(value,expected):
    if not isinstance(value,expected):raise TradingCycleSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()
serialize_identity=lambda v:_serialize(v,TradingCycleIdentity)
serialize_timing=lambda v:_serialize(v,TradingCycleTiming)
serialize_stage_record=lambda v:_serialize(v,TradingCycleStageRecord)
serialize_decision_trace=lambda v:_serialize(v,TradingDecisionTrace)
serialize_diagnostics=lambda v:_serialize(v,TradingCycleDiagnostics)
serialize_metrics=lambda v:_serialize(v,TradingCycleMetrics)
serialize_cycle=lambda v:_serialize(v,TradingCycle)
serialize_request=lambda v:_serialize(v,TradingCycleBuildRequest)
serialize_result=lambda v:_serialize(v,TradingCycleBuildResult)
serialize_policy=lambda v:_serialize(v,__import__('app.trading_cycle.policies',fromlist=['TradingCyclePolicy']).TradingCyclePolicy)

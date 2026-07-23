from app.backtest_run.exceptions import BacktestRunSerializationError
from app.backtest_run.models import *
def _s(v,t):
    if not isinstance(v,t):raise BacktestRunSerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_identity=lambda v:_s(v,BacktestRunIdentity)
serialize_policy=lambda v:_s(v,BacktestRunPolicy)
serialize_stage_result=lambda v:_s(v,BacktestRunStageResult)
serialize_request=lambda v:_s(v,BacktestRunRequest)
serialize_result=lambda v:_s(v,BacktestRunResult)

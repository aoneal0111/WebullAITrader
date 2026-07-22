from app.historical_replay.exceptions import HistoricalReplaySerializationError
from app.historical_replay.models import *
from app.historical_replay.policies import HistoricalReplayPolicy
def _s(v,t):
    if not isinstance(v,t):raise HistoricalReplaySerializationError(f"value must be {t.__name__}")
    return v.to_dict()
serialize_identity=lambda v:_s(v,HistoricalReplayIdentity)
serialize_event=lambda v:_s(v,HistoricalReplayEvent)
serialize_request=lambda v:_s(v,HistoricalReplayRequest)
serialize_event_result=lambda v:_s(v,HistoricalReplayEventResult)
serialize_progress=lambda v:_s(v,HistoricalReplayProgress)
serialize_result=lambda v:_s(v,HistoricalReplayResult)
serialize_policy=lambda v:_s(v,HistoricalReplayPolicy)
serialize_criteria=lambda v:_s(v,HistoricalReplayCriteriaResult)

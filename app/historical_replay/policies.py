from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.historical_replay.exceptions import HistoricalReplayValidationError
from app.historical_replay.models import HistoricalReplayFailureMode,HistoricalReplayOrdering
@dataclass(frozen=True,slots=True)
class HistoricalReplayPolicy:
    version:str="historical_replay_policy_v1";enabled:bool=False;strict_validation:bool=True;ordering:HistoricalReplayOrdering=HistoricalReplayOrdering.INPUT_ORDER;failure_mode:HistoricalReplayFailureMode=HistoricalReplayFailureMode.STOP_ON_FAILURE;allow_empty_events:bool=False;allow_duplicate_event_ids:bool=False;allow_duplicate_sequences:bool=False;require_unique_orchestrator_request_ids:bool=True;include_diagnostics:bool=True;maximum_events:int|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip() or self.version!=self.version.strip():raise HistoricalReplayValidationError("version must be a non-empty stripped string")
        for n in ("enabled","strict_validation","allow_empty_events","allow_duplicate_event_ids","allow_duplicate_sequences","require_unique_orchestrator_request_ids","include_diagnostics"):
            if not isinstance(getattr(self,n),bool):raise HistoricalReplayValidationError("policy flags must be boolean")
        if not isinstance(self.ordering,HistoricalReplayOrdering) or not isinstance(self.failure_mode,HistoricalReplayFailureMode):raise HistoricalReplayValidationError("policy enums are invalid")
        if self.maximum_events is not None and (isinstance(self.maximum_events,bool) or not isinstance(self.maximum_events,int) or self.maximum_events<1):raise HistoricalReplayValidationError("maximum_events must be positive or None")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {n:(getattr(self,n).value if n in ("ordering","failure_mode") else thaw_json_value(getattr(self,n)) if n=="metadata" else getattr(self,n)) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):
        try:d=dict(v);d["ordering"]=HistoricalReplayOrdering(d["ordering"]);d["failure_mode"]=HistoricalReplayFailureMode(d["failure_mode"]);return cls(**d)
        except HistoricalReplayValidationError:raise
        except (TypeError,ValueError) as exc:raise HistoricalReplayValidationError("invalid historical replay policy") from exc

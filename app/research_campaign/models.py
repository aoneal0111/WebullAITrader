"""Immutable records for deterministic coordination of experiments."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.experiment import ExperimentRequest,ExperimentResult
from app.research_campaign.exceptions import ResearchCampaignValidationError
def _text(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,str) or not value.strip() or value!=value.strip():raise ResearchCampaignValidationError(f"{name} must be a non-empty stripped string")
    return value
def _time(value,name):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise ResearchCampaignValidationError(f"{name} must be timezone-aware")
    return value
def _strings(value,name):
    if not isinstance(value,tuple) or any(not isinstance(x,str) or not x.strip() for x in value):raise ResearchCampaignValidationError(f"{name} must be immutable strings")
    return value
class ResearchCampaignStatus(StrEnum):
    COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class ResearchCampaignExperimentStatus(StrEnum):
    COMPLETED="COMPLETED";EXPERIMENT_FAILED="EXPERIMENT_FAILED";SKIPPED="SKIPPED"
@dataclass(frozen=True,slots=True)
class ResearchCampaignPolicy:
    enabled:bool=True;fail_fast:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise ResearchCampaignValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,value):return cls(value.get("enabled",True),value.get("fail_fast",False))
@dataclass(frozen=True,slots=True)
class ResearchCampaignIdentity:
    campaign_id:str
    def __post_init__(self):object.__setattr__(self,"campaign_id",_text(self.campaign_id,"campaign_id"))
    def to_dict(self):return {"campaign_id":self.campaign_id}
@dataclass(frozen=True,slots=True)
class ResearchCampaignExperimentIdentity:
    experiment_entry_id:str;experiment_id:str
    def __post_init__(self):
        object.__setattr__(self,"experiment_entry_id",_text(self.experiment_entry_id,"experiment_entry_id"));object.__setattr__(self,"experiment_id",_text(self.experiment_id,"experiment_id"))
    def to_dict(self):return {"experiment_entry_id":self.experiment_entry_id,"experiment_id":self.experiment_id}
@dataclass(frozen=True,slots=True)
class ResearchCampaignExperimentRequest:
    identity:ResearchCampaignExperimentIdentity;experiment_request:ExperimentRequest
    def __post_init__(self):
        if not isinstance(self.identity,ResearchCampaignExperimentIdentity) or not isinstance(self.experiment_request,ExperimentRequest):raise ResearchCampaignValidationError("campaign experiment contracts are invalid")
    def to_dict(self):return {"identity":self.identity.to_dict(),"experiment_request":self.experiment_request.to_dict()}
@dataclass(frozen=True,slots=True)
class ResearchCampaignRequest:
    identity:ResearchCampaignIdentity;experiments:tuple[ResearchCampaignExperimentRequest,...];policy:ResearchCampaignPolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,ResearchCampaignIdentity) or not isinstance(self.experiments,tuple) or any(not isinstance(x,ResearchCampaignExperimentRequest) for x in self.experiments) or not isinstance(self.policy,ResearchCampaignPolicy):raise ResearchCampaignValidationError("campaign request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise ResearchCampaignValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"experiments":[x.to_dict() for x in self.experiments],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class ResearchCampaignCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise ResearchCampaignValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class ResearchCampaignExperimentRecord:
    index:int;identity:ResearchCampaignExperimentIdentity;status:ResearchCampaignExperimentStatus;experiment_request:ExperimentRequest;experiment_result:ExperimentResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise ResearchCampaignValidationError("index must be non-negative integer")
        if not isinstance(self.identity,ResearchCampaignExperimentIdentity) or not isinstance(self.status,ResearchCampaignExperimentStatus) or not isinstance(self.experiment_request,ExperimentRequest):raise ResearchCampaignValidationError("experiment record contracts are invalid")
        if self.experiment_result is not None and not isinstance(self.experiment_result,ExperimentResult):raise ResearchCampaignValidationError("experiment_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"experiment_request":self.experiment_request.to_dict(),"experiment_result":self.experiment_result.to_dict() if self.experiment_result else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class ResearchCampaignSummary:
    total_experiments:int;processed_experiments:int;completed_experiments:int;failed_experiments:int;skipped_experiments:int
    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value<0:raise ResearchCampaignValidationError("summary counts must be non-negative integers")
        if self.completed_experiments+self.failed_experiments+self.skipped_experiments!=self.total_experiments or self.processed_experiments!=self.completed_experiments+self.failed_experiments:raise ResearchCampaignValidationError("summary counts are inconsistent")
    def to_dict(self):return {name:getattr(self,name) for name in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class ResearchCampaignResult:
    identity:ResearchCampaignIdentity;status:ResearchCampaignStatus;requested_at:datetime;completed_at:datetime;experiments:tuple[ResearchCampaignExperimentRecord,...];summary:ResearchCampaignSummary;criteria:ResearchCampaignCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,ResearchCampaignIdentity) or not isinstance(self.status,ResearchCampaignStatus) or not isinstance(self.summary,ResearchCampaignSummary) or not isinstance(self.criteria,ResearchCampaignCriteriaResult):raise ResearchCampaignValidationError("campaign result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.experiments,tuple) or any(not isinstance(x,ResearchCampaignExperimentRecord) for x in self.experiments):raise ResearchCampaignValidationError("experiment records must be immutable")
        if self.experiments and tuple(x.index for x in self.experiments)!=tuple(range(len(self.experiments))):raise ResearchCampaignValidationError("experiment record indexes are invalid")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"experiments":[x.to_dict() for x in self.experiments],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}

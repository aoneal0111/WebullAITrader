"""Immutable records for deterministic coordination of research campaigns."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.research_campaign import ResearchCampaignRequest,ResearchCampaignResult
from app.research_study.exceptions import ResearchStudyValidationError
def _text(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,str) or not value.strip() or value!=value.strip():raise ResearchStudyValidationError(f"{name} must be a non-empty stripped string")
    return value
def _time(value,name):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise ResearchStudyValidationError(f"{name} must be timezone-aware")
    return value
def _strings(value,name):
    if not isinstance(value,tuple) or any(not isinstance(x,str) or not x.strip() for x in value):raise ResearchStudyValidationError(f"{name} must be immutable strings")
    return value
class ResearchStudyStatus(StrEnum):
    COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class ResearchStudyCampaignStatus(StrEnum):
    COMPLETED="COMPLETED";CAMPAIGN_FAILED="CAMPAIGN_FAILED";SKIPPED="SKIPPED"
@dataclass(frozen=True,slots=True)
class ResearchStudyPolicy:
    enabled:bool=True;fail_fast:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise ResearchStudyValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,value):return cls(value.get("enabled",True),value.get("fail_fast",False))
@dataclass(frozen=True,slots=True)
class ResearchStudyIdentity:
    study_id:str
    def __post_init__(self):object.__setattr__(self,"study_id",_text(self.study_id,"study_id"))
    def to_dict(self):return {"study_id":self.study_id}
@dataclass(frozen=True,slots=True)
class ResearchStudyCampaignIdentity:
    campaign_entry_id:str;campaign_id:str
    def __post_init__(self):
        object.__setattr__(self,"campaign_entry_id",_text(self.campaign_entry_id,"campaign_entry_id"));object.__setattr__(self,"campaign_id",_text(self.campaign_id,"campaign_id"))
    def to_dict(self):return {"campaign_entry_id":self.campaign_entry_id,"campaign_id":self.campaign_id}
@dataclass(frozen=True,slots=True)
class ResearchStudyCampaignRequest:
    identity:ResearchStudyCampaignIdentity;campaign_request:ResearchCampaignRequest
    def __post_init__(self):
        if not isinstance(self.identity,ResearchStudyCampaignIdentity) or not isinstance(self.campaign_request,ResearchCampaignRequest):raise ResearchStudyValidationError("study campaign contracts are invalid")
    def to_dict(self):return {"identity":self.identity.to_dict(),"campaign_request":self.campaign_request.to_dict()}
@dataclass(frozen=True,slots=True)
class ResearchStudyRequest:
    identity:ResearchStudyIdentity;campaigns:tuple[ResearchStudyCampaignRequest,...];policy:ResearchStudyPolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,ResearchStudyIdentity) or not isinstance(self.campaigns,tuple) or any(not isinstance(x,ResearchStudyCampaignRequest) for x in self.campaigns) or not isinstance(self.policy,ResearchStudyPolicy):raise ResearchStudyValidationError("study request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise ResearchStudyValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"campaigns":[x.to_dict() for x in self.campaigns],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class ResearchStudyCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise ResearchStudyValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class ResearchStudyCampaignRecord:
    index:int;identity:ResearchStudyCampaignIdentity;status:ResearchStudyCampaignStatus;campaign_request:ResearchCampaignRequest;campaign_result:ResearchCampaignResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise ResearchStudyValidationError("index must be non-negative integer")
        if not isinstance(self.identity,ResearchStudyCampaignIdentity) or not isinstance(self.status,ResearchStudyCampaignStatus) or not isinstance(self.campaign_request,ResearchCampaignRequest):raise ResearchStudyValidationError("campaign record contracts are invalid")
        if self.campaign_result is not None and not isinstance(self.campaign_result,ResearchCampaignResult):raise ResearchStudyValidationError("campaign_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"campaign_request":self.campaign_request.to_dict(),"campaign_result":self.campaign_result.to_dict() if self.campaign_result else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class ResearchStudySummary:
    total_campaigns:int;processed_campaigns:int;completed_campaigns:int;failed_campaigns:int;skipped_campaigns:int
    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value<0:raise ResearchStudyValidationError("summary counts must be non-negative integers")
        if self.completed_campaigns+self.failed_campaigns+self.skipped_campaigns!=self.total_campaigns or self.processed_campaigns!=self.completed_campaigns+self.failed_campaigns:raise ResearchStudyValidationError("summary counts are inconsistent")
    def to_dict(self):return {name:getattr(self,name) for name in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class ResearchStudyResult:
    identity:ResearchStudyIdentity;status:ResearchStudyStatus;requested_at:datetime;completed_at:datetime;campaigns:tuple[ResearchStudyCampaignRecord,...];summary:ResearchStudySummary;criteria:ResearchStudyCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,ResearchStudyIdentity) or not isinstance(self.status,ResearchStudyStatus) or not isinstance(self.summary,ResearchStudySummary) or not isinstance(self.criteria,ResearchStudyCriteriaResult):raise ResearchStudyValidationError("study result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.campaigns,tuple) or any(not isinstance(x,ResearchStudyCampaignRecord) for x in self.campaigns):raise ResearchStudyValidationError("campaign records must be immutable")
        if self.campaigns and tuple(x.index for x in self.campaigns)!=tuple(range(len(self.campaigns))):raise ResearchStudyValidationError("campaign record indexes are invalid")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"campaigns":[x.to_dict() for x in self.campaigns],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}

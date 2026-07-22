"""Immutable records for deterministic coordination of research studies."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.research_study import ResearchStudyRequest,ResearchStudyResult
from app.research_program.exceptions import ResearchProgramValidationError
def _text(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,str) or not value.strip() or value!=value.strip():raise ResearchProgramValidationError(f"{name} must be a non-empty stripped string")
    return value
def _time(value,name):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise ResearchProgramValidationError(f"{name} must be timezone-aware")
    return value
def _strings(value,name):
    if not isinstance(value,tuple) or any(not isinstance(x,str) or not x.strip() for x in value):raise ResearchProgramValidationError(f"{name} must be immutable strings")
    return value
class ResearchProgramStatus(StrEnum):
    COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class ResearchProgramStudyStatus(StrEnum):
    COMPLETED="COMPLETED";STUDY_FAILED="STUDY_FAILED";SKIPPED="SKIPPED"
@dataclass(frozen=True,slots=True)
class ResearchProgramPolicy:
    enabled:bool=True;fail_fast:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise ResearchProgramValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,value):return cls(value.get("enabled",True),value.get("fail_fast",False))
@dataclass(frozen=True,slots=True)
class ResearchProgramIdentity:
    program_id:str
    def __post_init__(self):object.__setattr__(self,"program_id",_text(self.program_id,"program_id"))
    def to_dict(self):return {"program_id":self.program_id}
@dataclass(frozen=True,slots=True)
class ResearchProgramStudyIdentity:
    study_entry_id:str;study_id:str
    def __post_init__(self):
        object.__setattr__(self,"study_entry_id",_text(self.study_entry_id,"study_entry_id"));object.__setattr__(self,"study_id",_text(self.study_id,"study_id"))
    def to_dict(self):return {"study_entry_id":self.study_entry_id,"study_id":self.study_id}
@dataclass(frozen=True,slots=True)
class ResearchProgramStudyRequest:
    identity:ResearchProgramStudyIdentity;study_request:ResearchStudyRequest
    def __post_init__(self):
        if not isinstance(self.identity,ResearchProgramStudyIdentity) or not isinstance(self.study_request,ResearchStudyRequest):raise ResearchProgramValidationError("program study contracts are invalid")
    def to_dict(self):return {"identity":self.identity.to_dict(),"study_request":self.study_request.to_dict()}
@dataclass(frozen=True,slots=True)
class ResearchProgramRequest:
    identity:ResearchProgramIdentity;studies:tuple[ResearchProgramStudyRequest,...];policy:ResearchProgramPolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,ResearchProgramIdentity) or not isinstance(self.studies,tuple) or any(not isinstance(x,ResearchProgramStudyRequest) for x in self.studies) or not isinstance(self.policy,ResearchProgramPolicy):raise ResearchProgramValidationError("program request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise ResearchProgramValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"studies":[x.to_dict() for x in self.studies],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class ResearchProgramCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise ResearchProgramValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class ResearchProgramStudyRecord:
    index:int;identity:ResearchProgramStudyIdentity;status:ResearchProgramStudyStatus;study_request:ResearchStudyRequest;study_result:ResearchStudyResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise ResearchProgramValidationError("index must be non-negative integer")
        if not isinstance(self.identity,ResearchProgramStudyIdentity) or not isinstance(self.status,ResearchProgramStudyStatus) or not isinstance(self.study_request,ResearchStudyRequest):raise ResearchProgramValidationError("study record contracts are invalid")
        if self.study_result is not None and not isinstance(self.study_result,ResearchStudyResult):raise ResearchProgramValidationError("study_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"study_request":self.study_request.to_dict(),"study_result":self.study_result.to_dict() if self.study_result else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class ResearchProgramSummary:
    total_studies:int;processed_studies:int;completed_studies:int;failed_studies:int;skipped_studies:int
    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value<0:raise ResearchProgramValidationError("summary counts must be non-negative integers")
        if self.completed_studies+self.failed_studies+self.skipped_studies!=self.total_studies or self.processed_studies!=self.completed_studies+self.failed_studies:raise ResearchProgramValidationError("summary counts are inconsistent")
    def to_dict(self):return {name:getattr(self,name) for name in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class ResearchProgramResult:
    identity:ResearchProgramIdentity;status:ResearchProgramStatus;requested_at:datetime;completed_at:datetime;studies:tuple[ResearchProgramStudyRecord,...];summary:ResearchProgramSummary;criteria:ResearchProgramCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,ResearchProgramIdentity) or not isinstance(self.status,ResearchProgramStatus) or not isinstance(self.summary,ResearchProgramSummary) or not isinstance(self.criteria,ResearchProgramCriteriaResult):raise ResearchProgramValidationError("program result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.studies,tuple) or any(not isinstance(x,ResearchProgramStudyRecord) for x in self.studies):raise ResearchProgramValidationError("study records must be immutable")
        if self.studies and tuple(x.index for x in self.studies)!=tuple(range(len(self.studies))):raise ResearchProgramValidationError("study record indexes are invalid")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"studies":[x.to_dict() for x in self.studies],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}

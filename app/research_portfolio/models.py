"""Immutable records for deterministic coordination of research programs."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.research_program import ResearchProgramRequest,ResearchProgramResult
from app.research_portfolio.exceptions import ResearchPortfolioValidationError
def _text(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,str) or not value.strip() or value!=value.strip():raise ResearchPortfolioValidationError(f"{name} must be a non-empty stripped string")
    return value
def _time(value,name):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise ResearchPortfolioValidationError(f"{name} must be timezone-aware")
    return value
def _strings(value,name):
    if not isinstance(value,tuple) or any(not isinstance(x,str) or not x.strip() for x in value):raise ResearchPortfolioValidationError(f"{name} must be immutable strings")
    return value
class ResearchPortfolioStatus(StrEnum):
    COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class ResearchPortfolioProgramStatus(StrEnum):
    COMPLETED="COMPLETED";PROGRAM_FAILED="PROGRAM_FAILED";SKIPPED="SKIPPED"
@dataclass(frozen=True,slots=True)
class ResearchPortfolioPolicy:
    enabled:bool=True;fail_fast:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise ResearchPortfolioValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,value):return cls(value.get("enabled",True),value.get("fail_fast",False))
@dataclass(frozen=True,slots=True)
class ResearchPortfolioIdentity:
    portfolio_id:str
    def __post_init__(self):object.__setattr__(self,"portfolio_id",_text(self.portfolio_id,"portfolio_id"))
    def to_dict(self):return {"portfolio_id":self.portfolio_id}
@dataclass(frozen=True,slots=True)
class ResearchPortfolioProgramIdentity:
    program_entry_id:str;program_id:str
    def __post_init__(self):
        object.__setattr__(self,"program_entry_id",_text(self.program_entry_id,"program_entry_id"));object.__setattr__(self,"program_id",_text(self.program_id,"program_id"))
    def to_dict(self):return {"program_entry_id":self.program_entry_id,"program_id":self.program_id}
@dataclass(frozen=True,slots=True)
class ResearchPortfolioProgramRequest:
    identity:ResearchPortfolioProgramIdentity;program_request:ResearchProgramRequest
    def __post_init__(self):
        if not isinstance(self.identity,ResearchPortfolioProgramIdentity) or not isinstance(self.program_request,ResearchProgramRequest):raise ResearchPortfolioValidationError("portfolio program contracts are invalid")
    def to_dict(self):return {"identity":self.identity.to_dict(),"program_request":self.program_request.to_dict()}
@dataclass(frozen=True,slots=True)
class ResearchPortfolioRequest:
    identity:ResearchPortfolioIdentity;programs:tuple[ResearchPortfolioProgramRequest,...];policy:ResearchPortfolioPolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,ResearchPortfolioIdentity) or not isinstance(self.programs,tuple) or any(not isinstance(x,ResearchPortfolioProgramRequest) for x in self.programs) or not isinstance(self.policy,ResearchPortfolioPolicy):raise ResearchPortfolioValidationError("portfolio request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise ResearchPortfolioValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"programs":[x.to_dict() for x in self.programs],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class ResearchPortfolioCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise ResearchPortfolioValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class ResearchPortfolioProgramRecord:
    index:int;identity:ResearchPortfolioProgramIdentity;status:ResearchPortfolioProgramStatus;program_request:ResearchProgramRequest;program_result:ResearchProgramResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise ResearchPortfolioValidationError("index must be non-negative integer")
        if not isinstance(self.identity,ResearchPortfolioProgramIdentity) or not isinstance(self.status,ResearchPortfolioProgramStatus) or not isinstance(self.program_request,ResearchProgramRequest):raise ResearchPortfolioValidationError("program record contracts are invalid")
        if self.program_result is not None and not isinstance(self.program_result,ResearchProgramResult):raise ResearchPortfolioValidationError("program_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"program_request":self.program_request.to_dict(),"program_result":self.program_result.to_dict() if self.program_result else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class ResearchPortfolioSummary:
    total_programs:int;processed_programs:int;completed_programs:int;failed_programs:int;skipped_programs:int
    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value<0:raise ResearchPortfolioValidationError("summary counts must be non-negative integers")
        if self.completed_programs+self.failed_programs+self.skipped_programs!=self.total_programs or self.processed_programs!=self.completed_programs+self.failed_programs:raise ResearchPortfolioValidationError("summary counts are inconsistent")
    def to_dict(self):return {name:getattr(self,name) for name in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class ResearchPortfolioResult:
    identity:ResearchPortfolioIdentity;status:ResearchPortfolioStatus;requested_at:datetime;completed_at:datetime;programs:tuple[ResearchPortfolioProgramRecord,...];summary:ResearchPortfolioSummary;criteria:ResearchPortfolioCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,ResearchPortfolioIdentity) or not isinstance(self.status,ResearchPortfolioStatus) or not isinstance(self.summary,ResearchPortfolioSummary) or not isinstance(self.criteria,ResearchPortfolioCriteriaResult):raise ResearchPortfolioValidationError("portfolio result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.programs,tuple) or any(not isinstance(x,ResearchPortfolioProgramRecord) for x in self.programs):raise ResearchPortfolioValidationError("program records must be immutable")
        if self.programs and tuple(x.index for x in self.programs)!=tuple(range(len(self.programs))):raise ResearchPortfolioValidationError("program record indexes are invalid")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"programs":[x.to_dict() for x in self.programs],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}

from dataclasses import dataclass,field
from enum import StrEnum
from typing import Mapping
from app.authentication_runtime import AuthenticationRuntimeContext
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.credentials import CredentialRequest
from app.session import SessionRequest,SessionSnapshot
from app.session_bootstrap.exceptions import SessionBootstrapValidationError
class SessionBootstrapDecision(StrEnum):SUCCESS="SUCCESS";DISABLED="DISABLED";APPROVAL_REJECTED="APPROVAL_REJECTED";AUTHENTICATION_FAILED="AUTHENTICATION_FAILED";SESSION_CREATION_FAILED="SESSION_CREATION_FAILED"
def _s(v,n):
 if not isinstance(v,str) or not v.strip() or v!=v.strip():raise SessionBootstrapValidationError(f"{n} must be a non-empty stripped string")
 return v
@dataclass(frozen=True,slots=True)
class SessionBootstrapCriteriaResult:
 name:str;passed:bool;detail:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"name",_s(self.name,"criteria name"));object.__setattr__(self,"detail",_s(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise SessionBootstrapValidationError("criteria passed must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class SessionBootstrapRequest:
 bootstrap_id:str;authentication_attempt_id:str;credential_request:CredentialRequest;authentication_context:AuthenticationRuntimeContext;session_request:SessionRequest;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"bootstrap_id",_s(self.bootstrap_id,"bootstrap_id"));object.__setattr__(self,"authentication_attempt_id",_s(self.authentication_attempt_id,"authentication_attempt_id"))
  if not isinstance(self.credential_request,CredentialRequest):raise SessionBootstrapValidationError("credential_request must be CredentialRequest")
  if not isinstance(self.authentication_context,AuthenticationRuntimeContext):raise SessionBootstrapValidationError("authentication_context must be AuthenticationRuntimeContext")
  if not isinstance(self.session_request,SessionRequest):raise SessionBootstrapValidationError("session_request must be SessionRequest")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"bootstrap_id":self.bootstrap_id,"authentication_attempt_id":self.authentication_attempt_id,"credential_request":self.credential_request.to_dict(),"authentication_context":self.authentication_context.to_dict(),"session_request":self.session_request.to_dict(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["credential_request"]=CredentialRequest.from_dict(d["credential_request"]);d["authentication_context"]=AuthenticationRuntimeContext.from_dict(d["authentication_context"]);d["session_request"]=SessionRequest.from_dict(d["session_request"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class SessionBootstrapResult:
 bootstrap_id:str;approved_profile_id:str;authentication_result_id:str;session_id:str;success:bool;decision:SessionBootstrapDecision;session_handle:SessionSnapshot|None;criteria_results:tuple[SessionBootstrapCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for n in ("bootstrap_id","approved_profile_id","authentication_result_id","session_id"):object.__setattr__(self,n,_s(getattr(self,n),n))
  if not isinstance(self.success,bool) or self.success!=(self.decision is SessionBootstrapDecision.SUCCESS):raise SessionBootstrapValidationError("success must match decision")
  if self.success and not isinstance(self.session_handle,SessionSnapshot):raise SessionBootstrapValidationError("successful result requires session handle")
  if not self.success and self.session_handle is not None:raise SessionBootstrapValidationError("failed result cannot expose session handle")
  if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,SessionBootstrapCriteriaResult) for x in self.criteria_results):raise SessionBootstrapValidationError("criteria_results must be immutable criteria tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"bootstrap_id":self.bootstrap_id,"approved_profile_id":self.approved_profile_id,"authentication_result_id":self.authentication_result_id,"session_id":self.session_id,"success":self.success,"decision":self.decision.value,"session_handle":self.session_handle.to_dict() if self.session_handle else None,"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["decision"]=SessionBootstrapDecision(d["decision"]);d["session_handle"]=SessionSnapshot.from_dict(d["session_handle"]) if d["session_handle"] else None;d["criteria_results"]=tuple(SessionBootstrapCriteriaResult.from_dict(x) for x in d["criteria_results"]);return cls(**d)

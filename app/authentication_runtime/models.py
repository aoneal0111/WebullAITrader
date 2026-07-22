from dataclasses import dataclass,field
from typing import Mapping
from app.authentication_transport.models import AuthenticationTransportResult
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.credentials.models import CredentialRequest
def _s(v,n):
 if not isinstance(v,str) or not v.strip() or v!=v.strip():raise ValueError(f"{n} must be a non-empty stripped string")
 return v
@dataclass(frozen=True,slots=True)
class AuthenticationRuntimeContext:
 correlation_id:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):object.__setattr__(self,"correlation_id",_s(self.correlation_id,"correlation_id"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"correlation_id":self.correlation_id,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class AuthenticationRuntimeRequest:
 attempt_id:str;credential_request:CredentialRequest;context:AuthenticationRuntimeContext;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"attempt_id",_s(self.attempt_id,"attempt_id"))
  if not isinstance(self.credential_request,CredentialRequest):raise ValueError("credential_request must be CredentialRequest")
  if not isinstance(self.context,AuthenticationRuntimeContext):raise ValueError("context must be AuthenticationRuntimeContext")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"attempt_id":self.attempt_id,"credential_request":self.credential_request.to_dict(),"context":self.context.to_dict(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["credential_request"]=CredentialRequest.from_dict(d["credential_request"]);d["context"]=AuthenticationRuntimeContext.from_dict(d["context"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class AuthenticationRuntimeResult:
 attempt_id:str;success:bool;transport_result:AuthenticationTransportResult;context:AuthenticationRuntimeContext;policy_version:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"attempt_id",_s(self.attempt_id,"attempt_id"))
  if not isinstance(self.success,bool):raise ValueError("success must be boolean")
  if not isinstance(self.transport_result,AuthenticationTransportResult):raise ValueError("transport_result must be AuthenticationTransportResult")
  if self.success!=self.transport_result.success or self.attempt_id!=self.transport_result.attempt_id:raise ValueError("runtime result must match transport result")
  if not isinstance(self.context,AuthenticationRuntimeContext):raise ValueError("context must be AuthenticationRuntimeContext")
  object.__setattr__(self,"policy_version",_s(self.policy_version,"policy_version"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"attempt_id":self.attempt_id,"success":self.success,"transport_result":self.transport_result.to_dict(),"context":self.context.to_dict(),"policy_version":self.policy_version,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["transport_result"]=AuthenticationTransportResult.from_dict(d["transport_result"]);d["context"]=AuthenticationRuntimeContext.from_dict(d["context"]);return cls(**d)

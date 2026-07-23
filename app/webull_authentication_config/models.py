from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.http_runtime import HTTPMethod
from app.webull_authentication import WebullAuthenticationPolicy,WebullAuthenticationProfile
from app.webull_authentication_config.exceptions import WebullAuthenticationConfigurationFieldError
def _s(v,n):
 if not isinstance(v,str) or not v.strip() or v!=v.strip():raise WebullAuthenticationConfigurationFieldError(f"{n} must be a non-empty stripped string")
 return v
@dataclass(frozen=True,slots=True)
class WebullAuthenticationProfileConfiguration:
 configuration_id:str;profile_id:str;endpoint_url:str;http_method:HTTPMethod;username_field:str;password_field:str;device_id_field:str;username_reference:str;password_reference:str;device_reference:str;success_field_path:tuple[str,...];success_values:tuple[JSONValue,...];failure_message_field_path:tuple[str,...];verification_output_field_paths:tuple[tuple[str,tuple[str,...]],...];required_response_headers:tuple[str,...];static_headers:tuple[tuple[str,str],...];profile_metadata:Mapping[str,JSONValue];enabled:bool;strict_validation:bool;include_device_identifier:bool;require_success_indicator:bool;reject_unexpected_success_values:bool;policy_metadata:Mapping[str,JSONValue];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"configuration_id",_s(self.configuration_id,"configuration_id"));object.__setattr__(self,"profile_id",_s(self.profile_id,"profile_id"))
  if not isinstance(self.http_method,HTTPMethod):raise WebullAuthenticationConfigurationFieldError("http_method must be HTTPMethod")
  for n in ("success_field_path","success_values","failure_message_field_path","verification_output_field_paths","required_response_headers","static_headers"):
   if not isinstance(getattr(self,n),tuple):raise WebullAuthenticationConfigurationFieldError(f"{n} must be immutable")
  for n in ("enabled","strict_validation","include_device_identifier","require_success_indicator","reject_unexpected_success_values"):
   if not isinstance(getattr(self,n),bool):raise WebullAuthenticationConfigurationFieldError(f"{n} must be boolean")
  for n in ("profile_metadata","policy_metadata","metadata"):object.__setattr__(self,n,freeze_json_mapping(n,getattr(self,n)))
 def to_dict(self):return {"configuration_id":self.configuration_id,"profile_id":self.profile_id,"endpoint_url":self.endpoint_url,"http_method":self.http_method.value,"username_field":self.username_field,"password_field":self.password_field,"device_id_field":self.device_id_field,"username_reference":self.username_reference,"password_reference":self.password_reference,"device_reference":self.device_reference,"success_field_path":list(self.success_field_path),"success_values":thaw_json_value(self.success_values),"failure_message_field_path":list(self.failure_message_field_path),"verification_output_field_paths":[[n,list(p)] for n,p in self.verification_output_field_paths],"required_response_headers":list(self.required_response_headers),"static_headers":[list(x) for x in self.static_headers],"profile_metadata":thaw_json_value(self.profile_metadata),"enabled":self.enabled,"strict_validation":self.strict_validation,"include_device_identifier":self.include_device_identifier,"require_success_indicator":self.require_success_indicator,"reject_unexpected_success_values":self.reject_unexpected_success_values,"policy_metadata":thaw_json_value(self.policy_metadata),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  from app.webull_authentication_config.serializers import normalize_configuration
  return normalize_configuration(v)
@dataclass(frozen=True,slots=True)
class WebullAuthenticationProfileConfigurationResult:
 configuration_id:str;profile:WebullAuthenticationProfile;policy:WebullAuthenticationPolicy;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"configuration_id",_s(self.configuration_id,"configuration_id"))
  if not isinstance(self.profile,WebullAuthenticationProfile):raise WebullAuthenticationConfigurationFieldError("profile must be WebullAuthenticationProfile")
  if not isinstance(self.policy,WebullAuthenticationPolicy):raise WebullAuthenticationConfigurationFieldError("policy must be WebullAuthenticationPolicy")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"configuration_id":self.configuration_id,"profile":self.profile.to_dict(),"policy":self.policy.to_dict(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):return cls(v["configuration_id"],WebullAuthenticationProfile.from_dict(v["profile"]),WebullAuthenticationPolicy.from_dict(v["policy"]),v.get("metadata",{}))

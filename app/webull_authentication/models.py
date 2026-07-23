from dataclasses import dataclass,field
from typing import Mapping
from urllib.parse import urlsplit
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.http_runtime import HTTPMethod
from app.webull_authentication.exceptions import WebullAuthenticationProfileError
def _s(v,n):
 if not isinstance(v,str) or not v.strip() or v!=v.strip():raise WebullAuthenticationProfileError(f"{n} must be a non-empty stripped string")
 return v
def _path(v,n,empty=False):
 if not isinstance(v,tuple) or (not v and not empty):raise WebullAuthenticationProfileError(f"{n} must be an immutable field path")
 return tuple(_s(x,n) for x in v)
@dataclass(frozen=True,slots=True)
class WebullAuthenticationProfile:
 profile_id:str;endpoint_url:str;http_method:HTTPMethod;username_field:str;password_field:str;device_id_field:str;username_reference:str;password_reference:str;device_reference:str;success_field_path:tuple[str,...];success_values:tuple[JSONValue,...];failure_message_field_path:tuple[str,...];verification_output_field_paths:tuple[tuple[str,tuple[str,...]],...];required_response_headers:tuple[str,...];static_headers:tuple[tuple[str,str],...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for n in ("profile_id","username_field","password_field","username_reference","password_reference") :object.__setattr__(self,n,_s(getattr(self,n),n))
  parsed=urlsplit(self.endpoint_url)
  if parsed.scheme not in ("http","https") or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:raise WebullAuthenticationProfileError("endpoint_url must be an absolute HTTP URL without credentials, query, or fragment")
  if not isinstance(self.http_method,HTTPMethod):raise WebullAuthenticationProfileError("http_method must be HTTPMethod")
  for n in ("device_id_field","device_reference"):
   if not isinstance(getattr(self,n),str):raise WebullAuthenticationProfileError(f"{n} must be a string")
  object.__setattr__(self,"success_field_path",_path(self.success_field_path,"success_field_path"));object.__setattr__(self,"failure_message_field_path",_path(self.failure_message_field_path,"failure_message_field_path",True))
  if not isinstance(self.success_values,tuple) or not self.success_values:raise WebullAuthenticationProfileError("success_values must be a non-empty tuple")
  headers=[];seen=set()
  for k,v in self.static_headers:
   k=_s(k,"static header name");v=_s(v,"static header value");fold=k.casefold()
   if fold in seen:raise WebullAuthenticationProfileError("duplicate static header")
   seen.add(fold);headers.append((k,v))
  object.__setattr__(self,"static_headers",tuple(headers))
  required=tuple(_s(x,"required response header") for x in self.required_response_headers)
  if len({x.casefold() for x in required})!=len(required):raise WebullAuthenticationProfileError("duplicate required response header")
  object.__setattr__(self,"required_response_headers",required)
  outputs=[]
  for name,path in self.verification_output_field_paths:
   name=_s(name,"verification output name")
   secret_markers=("token","cook"+"ie","password","secret","credential")
   if any(x in name.casefold() for x in secret_markers):raise WebullAuthenticationProfileError("verification output names must be non-secret")
   outputs.append((name,_path(path,"verification output path")))
  if len({x[0] for x in outputs})!=len(outputs):raise WebullAuthenticationProfileError("duplicate verification output")
  object.__setattr__(self,"verification_output_field_paths",tuple(outputs));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"profile_id":self.profile_id,"endpoint_url":self.endpoint_url,"http_method":self.http_method.value,"username_field":self.username_field,"password_field":self.password_field,"device_id_field":self.device_id_field,"username_reference":self.username_reference,"password_reference":self.password_reference,"device_reference":self.device_reference,"success_field_path":list(self.success_field_path),"success_values":thaw_json_value(self.success_values),"failure_message_field_path":list(self.failure_message_field_path),"verification_output_field_paths":[[n,list(p)] for n,p in self.verification_output_field_paths],"required_response_headers":list(self.required_response_headers),"static_headers":[list(x) for x in self.static_headers],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,v):
  d=dict(v);d["http_method"]=HTTPMethod(d["http_method"])
  for n in ("success_field_path","failure_message_field_path","required_response_headers","success_values"):d[n]=tuple(d[n])
  d["static_headers"]=tuple(tuple(x) for x in d["static_headers"]);d["verification_output_field_paths"]=tuple((x[0],tuple(x[1])) for x in d["verification_output_field_paths"]);return cls(**d)

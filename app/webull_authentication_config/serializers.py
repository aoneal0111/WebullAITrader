from collections.abc import Mapping
from dataclasses import fields
from app.http_runtime import HTTPMethod
from app.webull_authentication_config.exceptions import WebullAuthenticationConfigurationValidationError
from app.webull_authentication_config.models import WebullAuthenticationProfileConfiguration
def _tuple(v,n):
 if not isinstance(v,(list,tuple)):raise WebullAuthenticationConfigurationValidationError(f"{n} must be a sequence")
 return tuple(v)
def normalize_configuration(value,strict_unknown_fields=True):
 if isinstance(value,WebullAuthenticationProfileConfiguration):return value
 if not isinstance(value,Mapping):raise WebullAuthenticationConfigurationValidationError("configuration must be a mapping or configuration model")
 allowed={f.name for f in fields(WebullAuthenticationProfileConfiguration)};unknown=set(value)-allowed
 if unknown and strict_unknown_fields:raise WebullAuthenticationConfigurationValidationError("configuration contains unknown fields")
 d={k:v for k,v in value.items() if k in allowed}
 try:
  d["http_method"]=HTTPMethod(d["http_method"])
  for n in ("success_field_path","success_values","failure_message_field_path","required_response_headers"):d[n]=_tuple(d[n],n)
  d["verification_output_field_paths"]=tuple((x[0],tuple(x[1])) for x in _tuple(d["verification_output_field_paths"],"verification_output_field_paths"))
  headers=d["static_headers"]
  if isinstance(headers,Mapping):headers=tuple((k,headers[k]) for k in sorted(headers,key=lambda x:(x.casefold(),x)))
  else:headers=tuple(tuple(x) for x in _tuple(headers,"static_headers"))
  d["static_headers"]=headers
  return WebullAuthenticationProfileConfiguration(**d)
 except WebullAuthenticationConfigurationValidationError:raise
 except Exception as exc:raise WebullAuthenticationConfigurationValidationError("configuration normalization failed") from exc
def serialize_configuration(v):return v.to_dict()
def serialize_result(v):return v.to_dict()

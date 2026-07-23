from app.http_pipeline import HTTPRequestOperation,PipelineContext
from app.webull_authentication.exceptions import *
from app.webull_authentication.validation import validate_dependencies,validate_request
class WebullAuthenticationRequestFactory:
 def __init__(self,profile,policy):validate_dependencies(profile,policy);self._profile=profile;self._policy=policy
 def create(self,authentication_request):
  r=validate_request(authentication_request)
  if not self._policy.enabled:raise WebullAuthenticationDisabledError("Webull authentication mapping is disabled")
  try:attempt=r.metadata["attempt_id"];correlation=r.metadata["correlation_id"]
  except Exception as exc:raise WebullAuthenticationRequestError("caller identifiers are required") from exc
  if not isinstance(attempt,str) or not attempt or not isinstance(correlation,str) or not correlation:raise WebullAuthenticationRequestError("caller identifiers must be non-empty strings")
  required=set(r.required_value_names);p=self._profile
  refs=(p.username_reference,p.password_reference)+((p.device_reference,) if self._policy.include_device_identifier else ())
  if any(x not in required for x in refs):raise WebullAuthenticationRequestError("required credential reference is missing")
  body={p.username_field:{"credential_reference":p.username_reference},p.password_field:{"credential_reference":p.password_reference}}
  if self._policy.include_device_identifier:
   if not p.device_id_field or not p.device_reference:raise WebullAuthenticationRequestError("device reference profile is incomplete")
   body[p.device_id_field]={"credential_reference":p.device_reference}
  return HTTPRequestOperation(f"{attempt}:webull-auth-request",p.http_method,p.endpoint_url,p.static_headers,(),body,PipelineContext(correlation),{"profile_id":p.profile_id})

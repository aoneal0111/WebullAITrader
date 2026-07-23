from app.authentication_transport import AuthenticationVerificationResult
from app.committee.models import JSONScalar
from app.webull_authentication.exceptions import *
from app.webull_authentication.validation import field_path,validate_dependencies,validate_request,validate_response
class WebullAuthenticationResponseVerifier:
 def __init__(self,profile,policy):validate_dependencies(profile,policy);self._profile=profile;self._policy=policy
 def verify(self,authentication_request,http_response):
  validate_request(authentication_request);r=validate_response(http_response)
  if not self._policy.enabled:raise WebullAuthenticationDisabledError("Webull authentication mapping is disabled")
  names={k.casefold() for k,_ in r.headers};missing=[x for x in self._profile.required_response_headers if x.casefold() not in names]
  if missing:raise WebullAuthenticationResponseError("required response header is missing")
  if not 200<=r.status_code<300:return AuthenticationVerificationResult(False,"HTTP_STATUS_REJECTED")
  try:value=field_path(r.body,self._profile.success_field_path)
  except WebullAuthenticationVerificationError:
   if self._policy.require_success_indicator:raise
   return AuthenticationVerificationResult(False,"SUCCESS_INDICATOR_MISSING")
  success=value in self._profile.success_values
  if not success and self._policy.reject_unexpected_success_values:return AuthenticationVerificationResult(False,"UNEXPECTED_SUCCESS_VALUE")
  outputs={}
  if success:
   for name,path in self._profile.verification_output_field_paths:
    item=field_path(r.body,path)
    if not isinstance(item,(str,int,float,bool,type(None))):raise WebullAuthenticationVerificationError("verification output must be scalar")
    outputs[name]=item
  reason="VERIFIED" if success else "REJECTED"
  return AuthenticationVerificationResult(success,reason,{"profile_id":self._profile.profile_id,"outputs":outputs})
